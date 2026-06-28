# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER

이 파일은 `docs/research_prompt.md`의 완전 교체용 MAIN EXECUTION PROMPT다.

이 문서는 새 점수표나 새 버전 놀이가 아니다. 목적은 단 하나다.

```text
CSV 전수 확보·전수 분류
→ BLIND 상태에서 천천히 뉴스 연구
→ BLIND 후보 분모 폐쇄
→ BLIND 후보 봉인
→ 그 뒤에만 D 가격 outcome 확인
→ 왜 맞고 틀렸는지 0622 gold처럼 학습 표본으로 분해
→ 두뇌가 먹을 record-level bundle 생성
```

아래 하위 호환 섹션에 남아 있는 `v30`, `v27`, `v26` 같은 이름은 repo/importer와 기존 validator가 알아보는 계약 라벨이다. 실행자는 버전 논쟁을 하지 않는다. 이 파일의 최상위 규칙은 **작업 순서 강제**다.

---

## 0. 절대 목적

이번 실행은 장전 추천글을 빠르게 만드는 작업이 아니다. 하루치 뉴스와 하루치 결과를 이용해, 이후 `news-scalping-lab` 두뇌가 반복 학습할 수 있는 연구 episode를 만드는 작업이다.

성공한 산출물은 다음 성질을 가진다.

```text
1. CSV 모든 row가 source_ledger와 row_disposition에 남아 있다.
2. material row는 fact/inference/candidate_screening으로 연구됐다.
3. final_watchlist는 candidate population 폐쇄 이후에만 생성됐다.
4. D 가격 outcome은 blind_seal_receipt 이후에만 접근됐다.
5. outcome_ledger는 전 시장 snapshot에서 만들어졌다.
6. outcome_leader_census는 실제 winner 전체를 닫는다.
7. outcome_to_news_audit는 winner별 1:1 reverse audit다.
8. postmortem은 맞고 틀림 감상문이 아니라 supervised population factory다.
9. brain_delta는 lesson memo가 아니라 record_type 기반 JSONL record다.
10. final Markdown은 다시 열어 parse한 숫자로만 ACCEPT_FULL을 판정한다.
```

가장 나쁜 산출물은 얕은 연구에 `ACCEPT_FULL`을 붙인 파일이다. 하지만 목표는 `ACCEPT_FULL`을 막는 것이 아니라, **처음부터 누락이 생기지 않도록 작업 순서를 고정하는 것**이다.

---

## 0.1 최종 import-ready bundle shell hard lock

최종 Markdown은 새 형식을 발명하지 않는다. `docs/example2.md`와 같은 repo-importable research bundle shell을 따른다. 이 규칙은 뒤쪽 하위 호환 섹션의 예시보다 우선한다.

파일 첫 byte는 반드시 YAML front matter 시작인 `---`여야 한다. H1 제목으로 시작하는 파일은 사람이 읽기 좋아도 repo import-ready bundle이 아니다.

최종 front matter 최소 계약:

```yaml
---
schema_version: "nslab.research_bundle.v11"
artifact_type: "research_episode_bundle"
episode_id: "NSLAB-YYYYMMDD-<INPUT_SHA8>"
trade_date: "YYYY-MM-DD"
bundle_status: "ACCEPT_FULL"
brain_eligible: true
direct_brain_ingest_ready: true
...
---
```

필수:

```text
front_matter.schema_version == nslab.research_bundle.v11
front_matter.artifact_type == research_episode_bundle
front_matter.episode_id is non-empty
front_matter.bundle_status == ACCEPT_FULL only after final reparse validator passes
bundle_manifest.json.schema_version in [nslab.bundle_manifest.v23, nslab.bundle_manifest.v11]
bundle_manifest.json.episode_id == front_matter.episode_id
direct_ingest_contract.json.schema_version == nslab.direct_ingest_contract.v1
direct_ingest_contract.json.episode_id == front_matter.episode_id
direct_ingest_contract.json.direct_brain_ingest_ready == true
direct_ingest_contract.json.automated_import_expected_to_pass == true
direct_ingest_contract.json.brain_eligible == true
direct_ingest_contract.json.requires_manual_research_review == false
direct_ingest_contract.json.requires_posthoc_prompt_repair == false
direct_ingest_contract.json.requires_human_semantic_review == false
direct_ingest_contract.json.fatal_blockers == []
direct_ingest_contract.json.hard_gate_summary.schema_contract_verified == true
direct_ingest_contract.json.hard_gate_summary.record_count_hash_parity_ready == true
direct_ingest_contract.json.hard_gate_summary.direct_ingest_contract_validation_parity_verified == true
direct_ingest_contract.json.hard_gate_summary.direct_ingest_contract_count_hash_parity_verified == true
direct_ingest_contract.json.hard_gate_summary.sample_weight_validation_status == "passed"
direct_ingest_contract.json.hard_gate_summary.issuer_day_weight_sum_mismatches == {}
direct_ingest_contract.json.hard_gate_summary.direct_event_weight_sum_mismatches == {}
direct_ingest_contract.json.hard_gate_summary.validator_exit_code == 0
direct_ingest_contract.json.hard_gate_summary.critical_error_count == 0
```

금지:

```text
파일이 "# NSLAB EPISODE BUNDLE" 같은 H1으로 시작함
schema_version: nslab.episode_bundle.v1
schema_version: nslab.episode_bundle.v11
research_episode.json.schema_version == nslab.research_episode.v1
bundle_manifest.json.schema_version == nslab.bundle_manifest.v1
direct_ingest_contract.json.status == READY_FOR_FINAL_REPARSE
direct_ingest_contract.json.status == PENDING_FINAL_REPARSE
direct_ingest_contract.json에 direct_brain_ingest_ready 필드 없음
direct_ingest_contract.json에 automated_import_expected_to_pass 필드 없음
direct_ingest_contract.json에 requires_human_semantic_review 필드 없음
자기 validation_report만 ACCEPT_FULL이고 repo import shell은 미완성
```

위 금지 패턴이 하나라도 있으면 연구 내용이 좋아도 `ACCEPT_FULL`이 아니다. 수리 가능한 렌더 오류이므로 front matter, `bundle_manifest.json`, `direct_ingest_contract.json`, `research_episode.json`을 repo-importable shell로 재렌더하고 다시 final Markdown을 re-open/re-parse한다.

수리 후에도 파일이 repo-importable shell이 아니면:

```text
bundle_status = QUARANTINE_IMPORT_SHELL_CONTRACT
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

---

## 1. 이번 파일의 사용 방식

새 웹 세션은 GitHub Raw의 이 파일 전체를 MAIN EXECUTION PROMPT로 읽는다. 새 세션은 sandbox 과거 파일을 모른다. 따라서 이 파일 내용이 GitHub repo의 `docs/research_prompt.md`에 실제 반영되어 있어야 한다.

직접 웹 세션에 붙이는 bootstrap은 별도 파일을 사용한다. 그 bootstrap은 날짜와 CSV 선택만 담당한다. 실제 연구 순서, BLIND/POSTMORTEM 분리, bundle contract는 이 MAIN PROMPT가 담당한다.

---

## 2. 최상위 작업 순서 — 절대 어기지 않는다

정상 거래일이고 outcome source가 있으면 아래 순서 외의 진행은 무효다.

```text
PHASE 0. Main prompt / CSV / calendar / P snapshot 확보
PHASE 1. CSV full parse와 row-level source 장부 생성
PHASE 2. BLIND 뉴스 연구 작업대 생성
PHASE 3. candidate population 폐쇄
PHASE 4. final_watchlist 작성
PHASE 5. BLIND packet 렌더·해시·봉인
PHASE 6. seal 검증 뒤 D outcome snapshot 확보
PHASE 7. full-market outcome ledger와 winner census 생성
PHASE 8. outcome_to_news 1:1 reverse audit
PHASE 9. postmortem supervised population 생성
PHASE 10. brain_delta record-level 생성
PHASE 11. report/canonical_graph/bundle 렌더
PHASE 12. final Markdown re-open/re-parse validator와 repair loop
```

금지 순서:

```text
final_watchlist → candidate_screening 보충
final_watchlist → fact_ledger 보충
D outcome 확인 → BLIND 후보 작성
outcome winner 확인 → 테마 universe 확장
scorecard 작성 → outcome_leader_census 작성
brain_delta 요약 작성 → supervised population 끼워맞춤
validation_report true 선언 → 나중에 숫자 맞춤
```

이 순서 위반은 단순 validation error가 아니라 연구 실패다. 위반했으면 앞으로 진행하지 말고 해당 phase로 되돌아가 population을 실제로 만든다.

---

## 3. 시간 압축 금지

이 작업은 오래 걸리는 것이 정상이다. 실행자는 빠른 종료를 목표로 하지 않는다.

다음을 금지한다.

```text
시간상 전체 row_disposition 생략
CSV 상위 일부만 보고 후보 생성
material row 몇 개만 보고 final 20개 작성
P snapshot 없이 D-1 context 추측
D outcome을 먼저 불러 가격 반응을 본 뒤 BLIND처럼 작성
outcome snapshot이 크다는 이유로 full-market ledger 생략
winner 일부만 보고 postmortem 작성
brain_delta를 교훈 10개로 축소
검증 실패를 repair 없이 quarantine으로 넘김
```

다음을 반드시 수행한다.

```text
뉴스 CSV full parse
뉴스 CSV 모든 row의 source_ledger record 생성
뉴스 CSV 모든 row의 row_disposition record 생성
material row queue 생성
material row queue 전체 연구
candidate_screening 전체 생성
final 후보마다 fact/inference/witness 생성
BLIND packet 실제 봉인
seal 검증 후 outcome snapshot 확보
outcome snapshot full parse
outcome_ledger full population 생성
winner census 전수 생성
winner별 reverse audit 생성
issuer-day/direct-event/theme/pair/error/newsless/negative brain_delta 생성
final Markdown reparse validator 실행
repair loop 후 재검증
```

Python이나 파일 분석 도구는 금지되지 않는다. 단, parser는 **전수 장부를 만들기 위한 도구**이지, 연구를 대체하는 핑계가 아니다. parser가 row를 세면 연구자는 material row queue를 읽고 사건의 경제 작동 원리를 판단해야 한다.

---

## 4. PHASE 0 — 입력 확보와 오염 방지

### 4.1 Main prompt 확보

GitHub Raw `docs/research_prompt.md`를 확보한다. HTML preview는 파일 존재와 Raw 경로 확인용일 뿐이다. Raw 본문을 실제로 읽고, byte_size와 sha256을 기록한다.

이 파일의 상단 제목 또는 `research_prompt_revision`이 `nslab.gold_phase_machine.direct_csv_research.locked` 계열이 아니면 연구를 진행하지 않는다. 이 경우 최소 Markdown 파일을 생성한다.

```text
status = MAIN_PROMPT_VERSION_MISMATCH
brain_eligible = false
outcome_research_performed = false
reason = Raw prompt is not the locked direct CSV research prompt
```

### 4.2 CSV 확보

선택된 `news_YYYYMMDD.csv`는 Raw URL에서 실제 다운로드한다.

필수 기록:

```text
input_file
input_sha256
input_byte_size
csv_row_count
parsed_row_count
columns
min_published_at
max_published_at
time_unverified_rows
control_char_count
```

`curl`, `urllib`, `requests`, DNS, socket 실패는 파일 부재가 아니다. web/browser/download fallback을 모두 시도하기 전에는 ACQUIRE_FAILED를 선언하지 않는다.

### 4.3 공식 거래일 확인

파일명 날짜 D, CSV 내부 게시시각, 공식 거래일 여부를 함께 사용한다.

- D가 공식 거래일이면 현재 CSV 하나만 연구 입력이다.
- D가 공식 비거래일이면 일반 연구를 하지 않고 deferred Markdown만 만든다.
- 공식 거래일인데 D 가격 snapshot이 없으면 `PRICE_SOURCE_MISSING`으로 BLIND만 정상 수행하고 postmortem은 보류한다.

### 4.4 P snapshot 확보

BLIND에서는 직전 거래일 P snapshot만 사용할 수 있다. P snapshot은 ranking context로만 사용한다. D 가격, D winner, D high_return, D volume, D amount_rank는 seal 전 접근 금지다.

---

## 5. PHASE 1 — CSV full parse와 row-level source 장부

이 phase의 목적은 “뉴스를 조금 보는 것”이 아니라 입력 분모를 닫는 것이다.

### 5.1 source_ledger.jsonl

CSV의 모든 row마다 하나의 row-level source record를 만든다.

최소 필드:

```json
{
  "source_id": "SRC-000001",
  "source_type": "NEWS_CSV_ROW",
  "input_file": "news_YYYYMMDD.csv",
  "input_sha256": "",
  "row_index": 1,
  "published_at_kst": "",
  "title": "",
  "url": null,
  "raw_row_sha256": "",
  "time_verified": true,
  "used_in_blind": true
}
```

필수 equality:

```text
source_ledger_news_row_count == csv_row_count
source_ledger_missing_row_count == 0
source_ledger_duplicate_row_id_count == 0
```

파일 단위 source 몇 개만 쓰고 뉴스 row-level source를 생략하면 다음 phase로 갈 수 없다.

### 5.2 row_disposition.jsonl

CSV의 모든 row마다 하나의 disposition record를 만든다.

허용 disposition 예:

```text
DIRECT_ISSUER_MATERIAL
DIRECT_ISSUER_SECONDARY
THEME_POLICY_INDUSTRY_EVENT
MARKET_STATE_REGIME
D1_CONTINUATION_SIGNAL
DISCLOSURE_OR_MARKET_NOTICE
BODY_TABLE_OR_LIST_AUDIT
DUPLICATE
LOW_SIGNAL_CONTEXT
NON_MARKET_NEWS
NON_KR_OR_NON_LISTED_CONTEXT
TIME_UNVERIFIED_RETAINED
PARSER_AMBIGUOUS_REVIEWED
```

필수 equality:

```text
row_disposition_count == csv_row_count
row_disposition_unassigned_count == 0
row_disposition_duplicate_assignment_count == 0
```

`LOW_SIGNAL_CONTEXT`, `DUPLICATE`, `NON_MARKET_NEWS`도 버리지 않는다. 모두 장부에 남긴다.

### 5.3 material review queue

아래 row는 material review queue에 반드시 들어간다.

```text
DIRECT_ISSUER_MATERIAL
DIRECT_ISSUER_SECONDARY
THEME_POLICY_INDUSTRY_EVENT
MARKET_STATE_REGIME
D1_CONTINUATION_SIGNAL
DISCLOSURE_OR_MARKET_NOTICE
BODY_TABLE_OR_LIST_AUDIT
PARSER_AMBIGUOUS_REVIEWED
```

이 queue는 후보 생성을 위한 실제 연구 작업대다. `candidate_screening`을 만들기 전에 queue 전체를 닫는다.

필수 기록:

```text
material_review_queue_count
material_reviewed_count
material_review_unreviewed_count == 0
```

`material_reviewed`는 queue membership을 그대로 복사한 boolean이 아니다. 각 material row는 실제 검토 record를 가져야 한다.

필수 per-row 증거:

```text
review_decision
exact_quote
quote_found_in_source_row
issuer_binding 또는 rejection_reason
local_predicate_owner 또는 why_no_local_predicate_owner
```

필수 anti-fake equality:

```text
material_review_auto_boolean_count == 0
material_review_missing_decision_count == 0
material_review_missing_quote_count == 0
material_review_missing_binding_or_rejection_count == 0
```

### 5.4 provisional_hypothesis.jsonl — BLIND 중간 가설 흡수

CSV를 전수로 읽는 동안 특정 종목, 테마, continuation 가능성이 떠오르는 것은 정상이다. 다만 그 생각은 final 후보나 rank가 아니라 `provisional_hypothesis.jsonl`에만 기록한다.

목적:

```text
읽으면서 생긴 BLIND intuition을 버리지 않는다.
하지만 intuition이 row population, material review, candidate_screening을 대체하지 못하게 한다.
```

최소 필드:

```json
{
  "hypothesis_id": "HYP-...",
  "created_after_source_rows_seen_count": 0,
  "trigger_source_ids": [],
  "trigger_material_review_ids": [],
  "hypothesis_type": "DIRECT_ISSUER | THEME | CONTINUATION | MARKET_STATE | ARCHETYPE",
  "candidate_company_or_archetype": "",
  "ticker_or_null": null,
  "reasoning_blind_only": "",
  "allowed_use": "NAVIGATION_AND_COMPARISON_ONLY",
  "promotion_status": "UNRESOLVED | PROMOTED_TO_SCREENING | REJECTED_AFTER_REVIEW",
  "promoted_screening_id_or_null": null,
  "rejected_reason_or_null": null,
  "source_phase": "BLIND"
}
```

금지:

```text
provisional_hypothesis에 rank, final_rank, proposed_final_rank, score, final_order, selected_order 필드 넣기
provisional_hypothesis를 final_watchlist 또는 candidate_screening의 정렬키로 사용
가설에 나온 회사·ticker만 남기고 나머지 material row 검토를 생략
P snapshot high_return/amount/turnover만으로 hypothesis를 final 후보처럼 승격
```

필수 equality:

```text
provisional_hypothesis_with_rank_field_count == 0
provisional_hypothesis_used_as_final_count == 0
hypothesis_driven_row_filter_count == 0
```

---

## 6. PHASE 2 — BLIND 뉴스 연구 작업대

이 phase에서는 D outcome을 모른다. 모든 판단은 CSV, P snapshot, cutoff-safe source만 사용한다.

### 6.1 entity_resolution.jsonl / entity_ledger_blind.jsonl

material review row에서 회사명·ticker·issuer를 추출하되, 무리한 binding을 금지한다.

반드시 분리:

```text
정확한 상장 issuer
비상장/해외/일반 기업
그룹명/브랜드명
상품명/자연현상/지명
표/리스트의 구성원
다른 회사 기사에 언급된 회사
제조사 단순언급
참석자 단순언급
정치·거시 regime row
```

ticker-like 숫자, CSS color, 약어, 부분 문자열은 issuer로 자동 승격하지 않는다.

### 6.2 fact_ledger_blind.jsonl

각 material row에서 atomic fact를 만든다. fact는 실제 CSV row text의 substring quote를 가져야 한다.

최소 필드:

```json
{
  "fact_id": "FACT-...",
  "source_row_id": "SRC-...",
  "candidate_company": "",
  "ticker": "",
  "exact_quote": "",
  "quote_found_in_source_row": true,
  "fact_class": "",
  "source_phase": "BLIND",
  "cutoff_safe": true
}
```

### 6.3 inference_ledger_blind.jsonl

inference는 fact에서 나온 경제 변수만 사용한다. 템플릿 문장 금지.

최소 필드:

```json
{
  "inference_id": "INF-...",
  "source_fact_ids": ["FACT-..."],
  "candidate_company": "",
  "ticker": "",
  "economic_variable_changed": "REVENUE | MARGIN | COST | CAPITAL_POLICY | APPROVAL_PROBABILITY | CONTROL_PREMIUM | MARKET_MEMORY | RISK_AVOIDANCE | NONE",
  "mechanism_sentence": "",
  "mechanism_supported": true,
  "unsupported_inserted_concepts": [],
  "source_phase": "BLIND"
}
```

### 6.4 event/observation population

material fact는 observation으로 묶는다.

필수 population:

```text
direct_issuer_observations
theme_policy_observations
market_state_observations
continuation_observations
body_table_audit_observations
rejected_semantic_observations
```

이 단계가 비면 final 후보를 만들 수 없다. material row가 실제로 모두 low-signal이면 그 사실을 `ledger_population_audit.json`에 숫자로 증명한다.

---

## 7. PHASE 3 — candidate population 폐쇄

`candidate_screening.jsonl`은 final 후보 20개 목록이 아니다. 연구자가 검토한 observation 전체의 작업대다.

각 candidate/screening row 최소 필드:

```json
{
  "screening_id": "SCR-...",
  "candidate_id": "CAND-...",
  "source_observation_ids": [],
  "source_fact_ids": [],
  "source_inference_ids": [],
  "ticker": "",
  "company": "",
  "candidate_path": "DIRECT_ISSUER | THEME_BENEFICIARY | MARKET_STATE | CONTINUATION | AUDIT_ONLY",
  "screening_decision": "INCLUDE | WATCH_SECONDARY | EXCLUDE | AUDIT_ONLY | REJECT_SEMANTIC_FALSE_POSITIVE",
  "decision_reason_specific": "",
  "why_not_final_if_rejected": "",
  "semantic_risk_flags": [],
  "source_phase": "BLIND"
}
```

필수 equality:

```text
candidate_screening_material_coverage_count >= material_observation_count
unscreened_material_observation_count == 0
candidate_screening_final_only_mode == false
```

`candidate_screening.jsonl`은 final rank를 숨겨 둔 seed table이 아니다. 여기에는 observation의 승격·강등·보류 사유만 기록한다.

금지 필드:

```text
rank
final_rank
proposed_final_rank
watchlist_rank
final_order
selected_order
preseed_rank
```

금지 패턴:

```text
curated = [...] 또는 selected_codes = [...] 같은 predeclared final list를 먼저 만들고 candidate_screening을 채우기
final_codes_order를 BLIND 산출물에 저장하기
candidate_screening을 다시 열었다는 이유만으로 preseed rank를 정렬키로 쓰기
keyword hit list, P snapshot leader list, manually selected ticker list를 candidate universe로 사용하기
```

provisional_hypothesis에서 승격되는 경우에도 반드시 다음을 만족해야 한다.

```text
promotion_from_hypothesis_id가 존재
source_observation_ids가 존재
source_fact_ids 또는 no_fact_rejection_reason이 존재
candidate_screening row가 material_review record와 연결
승격되지 않은 가설은 REJECTED_AFTER_REVIEW 또는 UNRESOLVED로 남김
```

필수 anti-reward-hack equality:

```text
predeclared_final_candidate_list_count == 0
candidate_screening_rank_field_count == 0
candidate_screening_preseed_rank_count == 0
candidate_screening_unlinked_to_material_review_count == 0
provisional_hypothesis_promoted_without_screening_count == 0
```

후보를 지우지 않는다. 틀릴 가능성이 큰 후보도 `WATCH_SECONDARY`, `EXCLUDE`, `AUDIT_ONLY`, `negative_control_source`로 남긴다.

---

## 8. PHASE 4 — final_watchlist는 마지막 BLIND 산출물이다

final_watchlist는 candidate population이 닫힌 뒤에만 만든다.

final_watchlist는 저장된 `candidate_screening.jsonl`을 다시 열어 parse한 뒤 새로 작성한다. 단, `candidate_screening` 내부의 preseed rank나 hidden order를 사용하면 무효다.

필수 ranking 산출물:

```text
candidate_ranking_audit.jsonl
```

`candidate_ranking_audit.jsonl`은 final 20개만 기록하는 scorecard가 아니다. ranking 가능한 후보 전체에 대해 final 포함/제외 판단을 닫는 장부다.

rankable candidate는 다음으로 정의한다.

```text
candidate_screening.screening_decision in [
  INCLUDE,
  WATCH_SECONDARY
]
```

검증 카운터는 다음처럼 계산한다.

```text
candidate_screening_include_or_watch_count =
  count(candidate_screening rows where screening_decision in [INCLUDE, WATCH_SECONDARY])

candidate_ranking_audit_rankable_count =
  count(candidate_ranking_audit rows whose source_screening_id maps to a rankable candidate)
```

이 count는 raw `candidate_screening.jsonl` row 기준이다. ticker, issuer, candidate_id, company_name으로 dedupe한 뒤 expected를 줄이면 안 된다.

금지:

```text
candidate_screening_include_or_watch_count를 unique ticker count로 재정의
candidate_screening_include_or_watch_count를 candidate_ranking_audit row count에 맞춰 재작성
INCLUDE/WATCH_SECONDARY row 143개를 unique candidate 100개로 줄이고 expected=100으로 validator 통과
동일 ticker 중복 row를 ranking audit에서 생략하면서 source_screening_id별 탈락 사유를 남기지 않음
```

같은 ticker가 여러 source_screening_id로 등장해도 각 rankable row는 `candidate_ranking_audit.jsonl`에 별도 line으로 남긴다. 동일 ticker 중 final에 하나만 들어가는 경우, 나머지 같은 ticker rankable row는 `included_in_final=false`와 `why_not_final_if_excluded`로 중복 탈락 사유를 남긴다.

각 ranking audit row는 다음을 기록한다.

```text
candidate_id
source_screening_id
included_in_final
rank_if_final_or_null
ranking_inputs
primary_fact_strength
novelty_assessment
issuer_binding_quality
safe_D1_context_used
pairwise_comparison_refs
rank_reason
why_not_final_if_excluded
```

위 필드명은 canonical contract다. 최종 bundle에 들어가기 전 intermediate alias는 반드시 canonical field로 normalize한다.

금지 alias:

```text
screening_id only without source_screening_id
final_rank
ranking_factors
ranking_score_blind
```

허용 변환:

```text
screening_id -> source_screening_id
final_rank -> rank_if_final_or_null
ranking_factors + ranking_score_blind -> ranking_inputs
ranking_score_blind explanation -> rank_reason
```

final row:

```text
included_in_final == true
rank_if_final_or_null is integer 1..N
why_not_final_if_excluded is null or ""
```

non-final rankable row:

```text
included_in_final == false
rank_if_final_or_null == null
why_not_final_if_excluded is non-empty
```

금지:

```text
final_codes_order 필드 작성
candidate_screening.proposed_final_rank 정렬
curated/manual/preselected ticker list 순서 유지
P snapshot high_return/amount/turnover rank만으로 final 승격
```

필수:

```text
candidate_ranking_audit_rankable_count == candidate_screening_include_or_watch_count
candidate_ranking_audit_final_count == final_watchlist_count
candidate_ranking_audit_missing_rankable_count == 0
candidate_ranking_audit_excluded_without_reason_count == 0
candidate_ranking_audit_final_without_rank_count == 0
candidate_ranking_audit_nonfinal_with_rank_count == 0
candidate_ranking_audit_missing_canonical_field_count == 0
candidate_ranking_audit_legacy_alias_field_count == 0
candidate_ranking_audit_final_rank_sequence_verified == true
final_watchlist_count <= 20
rank == 1..N continuous
duplicate_ticker_count == 0 unless preferred share/common share explicitly separated
filler_candidate_count == 0
final_watchlist_from_preseed_rank_count == 0
final_codes_order_present == false
final_watchlist_from_reparsed_candidate_screening_without_preseed == true
```

non-final rankable candidate는 삭제하지 않는다. 반드시 다음 형태로 `candidate_ranking_audit.jsonl`에 남긴다.

```json
{
  "included_in_final": false,
  "rank_if_final_or_null": null,
  "why_not_final_if_excluded": "final 후보 대비 catalyst urgency, issuer binding, novelty, pairwise strength가 낮음"
}
```

각 final item은 다음을 가져야 한다.

```json
{
  "rank": 1,
  "candidate_id": "CAND-...",
  "ticker": "",
  "company": "",
  "candidate_path": "DIRECT_ISSUER | THEME_BENEFICIARY | MARKET_STATE | CONTINUATION",
  "source_fact_ids": [],
  "mechanism_inference_id": "INF-...",
  "why_now": "",
  "red_team": "",
  "final_evidence_witness_id": "FEW-..."
}
```

### 8.1 path별 evidence rule

DIRECT_ISSUER:

```text
candidate == article_subject_company 또는 candidate == local_predicate_owner
quote가 candidate의 직접 경제행동을 지지
다른 회사 기사/참석자/제조사/표 구성원/단순 언급은 final 불가
```

THEME_BENEFICIARY:

```text
candidate가 기사 주어가 아닐 수 있음
하지만 cutoff-safe bridge_fact_id와 relation_class가 필수
outcome 이후 오른 종목을 사후 편입 금지
```

MARKET_STATE:

```text
정치/거시/거래소/유동성 regime이 현재 장전 의사결정에 영향을 주는 사건이어야 함
직접 회사 수주처럼 포장 금지
```

CONTINUATION:

```text
P snapshot 또는 cutoff-safe D-1 정보만 사용
D outcome 본 뒤 continuation 후보 추가 금지
```

### 8.2 final evidence witness

각 final item은 `final_evidence_witness.jsonl`에 정확히 하나의 witness를 가진다. witness 필드명은 하위 호환 계약의 exact schema를 따른다. 축약 alias로 대체하지 않는다.

---

## 9. PHASE 5 — BLIND packet 봉인

봉인 전 생성·해시 대상:

```text
blind_report.md
blind_prediction.json
source_ledger.jsonl
row_disposition.jsonl
entity_resolution.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
candidate_semantic_witness.jsonl
final_evidence_witness.jsonl
final_semantic_audit.jsonl
market_state_override_audit.jsonl
body_table_candidate_generation_audit.jsonl
ledger_population_audit.json
```

`blind_report.md`에는 D outcome, high_return, upper_limit, hit/miss, actual leader, D scorecard를 넣지 않는다.

`blind_seal_receipt.json`은 outcome byte/header/hash/row/stat 접근 전에 생성되고 검증되어야 한다.

필수 counters:

```text
preseal_outcome_download_count == 0
preseal_outcome_header_read_count == 0
preseal_outcome_sha256_count == 0
preseal_outcome_row_count_count == 0
preseal_outcome_parse_count == 0
preseal_outcome_winner_census_count == 0
```

이 중 하나라도 0이 아니면 BLIND는 오염이다. 사후 수리로 ACCEPT_FULL을 만들 수 없다.

---

## 10. PHASE 6 — seal 이후 D outcome 확보

D outcome snapshot은 blind_seal_receipt 검증 뒤에만 연다.

필수 기록:

```text
outcome_snapshot_url
outcome_snapshot_sha256
outcome_snapshot_byte_size
outcome_row_count
schema/header
price_adjustment_status
market coverage
quarantined_price_rows
```

공식 거래일인데 D outcome snapshot이 없으면:

```text
status = PRICE_SOURCE_MISSING
blind_valid = true
postmortem_status = NOT_PERFORMED_PRICE_SOURCE_MISSING
brain_delta_status = NOT_PERFORMED_PRICE_SOURCE_MISSING
```

이 경우 BLIND artifact와 seal은 정상 bundle로 남긴다.

---

## 11. PHASE 7 — full-market outcome ledger와 winner census

`outcome_ledger.jsonl`은 전 시장 snapshot row에서 생성한다. 포털 상승률 목록이나 기사 목록으로 대체하지 않는다.

필수:

```text
outcome_ledger_count == verified_D_snapshot_tradable_row_count + quarantined_rows_count
unique_ticker_count == outcome_ledger_unique_ticker_count
```

`outcome_leader_census.jsonl`은 다음 집합을 전수 포함한다.

```text
upper_limit_touched
upper_limit_closed
upper_limit_released
one_price_upper_limit
high_return_pct >= 20
high_return_pct >= 15
high_return_pct >= 10
amount_rank top group
turnover_rank top group when available
```

최소 필드:

```json
{
  "outcome_leader_id": "LEAD-...",
  "ticker": "",
  "company": "",
  "high_return_pct": 0.0,
  "close_return_pct": 0.0,
  "outcome_class": "UPPER_LIMIT_CLOSED | UPPER_LIMIT_TOUCHED_RELEASED | HIGH20 | HIGH15 | HIGH10",
  "amount_rank": null,
  "turnover_rank": null,
  "price_label_quality": "verified | quarantined"
}
```

final_watchlist scorecard만 보고 끝내면 안 된다.

---

## 12. PHASE 8 — outcome_to_news 1:1 reverse audit

`outcome_to_news_audit.jsonl`은 `outcome_leader_census.jsonl`과 1:1이다. final 후보 scorecard row를 섞지 않는다.

필수 equality:

```text
outcome_to_news_audit_count == outcome_leader_census_count
outcome_to_news_audit_extra_final_scorecard_rows == 0
outcome_leader_without_reverse_audit_count == 0
```

각 row 최소 필드:

```json
{
  "audit_id": "OUTNEWS-...",
  "outcome_leader_id": "LEAD-...",
  "ticker": "",
  "company": "",
  "was_in_final_watchlist": false,
  "was_in_candidate_screening": false,
  "sealed_source_match": "DIRECT_MATCH | THEME_BRIDGE | MARKET_STATE | CONTINUATION | NONE",
  "classification": "HIT | RANKING_MISS | CANDIDATE_GENERATION_MISS | SCREENED_OUT_BUT_WINNER | NEWSLESS_OR_UNEXPLAINED | SEMANTIC_FALSE_POSITIVE | TIMING_IMPOSSIBLE | OUTCOME_ONLY_RELATION_NOT_TRAINING_ELIGIBLE",
  "matched_source_row_ids": [],
  "matched_fact_ids": [],
  "no_hallucinated_catalyst": true,
  "training_eligible": false,
  "available_from": "next_trade_dateT00:00:00+09:00"
}
```

뉴스 없는 winner에 가짜 catalyst를 붙이지 않는다. outcome-only 관계는 cutoff-safe bridge로 승격하지 않는다.

---

## 13. PHASE 9 — postmortem supervised population

POSTMORTEM은 틀린 이유를 산문으로 쓰는 단계가 아니다. 아래 모집단을 record source로 만든다.

필수 population:

```text
issuer_day_cases
direct_event_cases
theme_formation_cases
beneficiary_discovery_cases
blind_leader_preference_pairs
candidate_generation_error_cases
ranking_error_cases
semantic_binding_error_cases
negative_control_cases
newsless_or_unexplained_cases
research_questions
```

### 13.1 issuer-day

같은 `trade_date + ticker`의 직접 event를 하나의 issuer-day로 묶는다.

필수:

```text
issuer_day_case_count >= unique_ticker_count_from_material_direct_observations_with_resolution
issuer_day_event_weight_sum_per_ticker == 1.0 or documented no_tradable/quarantine
```

### 13.2 direct-event

candidate_screening의 각 material event에 outcome label을 결합한다.

필수:

```text
direct_event_case_count >= candidate_screening_material_count
case has screening_decision, sealed_fact_ids, D_response, training_eligible
```

### 13.3 errors and negative controls

실제 winner가 BLIND 후보에 없으면 candidate_generation_error_case다. 후보에는 있었지만 final에 없으면 ranking_error_case 또는 screened_out_but_winner다. 뉴스가 없으면 newsless_or_unexplained_case다. cutoff 이후 공시가 원인이면 TIMING_IMPOSSIBLE이고 training_eligible=false다.

틀린 후보도 삭제하지 않는다. negative control은 두뇌의 브레이크다.

---

## 14. PHASE 10 — brain_delta record-level 생성

`brain_delta.jsonl`은 요약 교훈이 아니다. 각 line은 record다.

허용 record_type:

```text
supervised_issuer_day_case
supervised_direct_event_case
supervised_theme_formation_case
beneficiary_discovery_case
theme_formation_case
blind_leader_preference_pair
candidate_generation_error_case
candidate_ranking_error_case
ranking_error_case
row_disposition_error_case
entity_resolution_error_case
context_market_state_or_fact_case
counterexample
negative_control_case
newsless_or_unexplained_case
event_ticker_edge
company_memory_delta
memory_claim
mechanism_memory
research_question
```

최종 bundle의 `brain_delta.jsonl`에는 위 canonical record_type만 쓴다. 아래 non-canonical alias는 최종 산출 전에 반드시 변환한다.

```text
final_watchlist_supervised_outcome -> supervised_issuer_day_case 또는 supervised_direct_event_case
outcome_to_news_reverse_audit_case -> beneficiary_discovery_case, newsless_or_unexplained_case, supervised_issuer_day_case 중 의미에 맞는 canonical type
candidate_generation_miss -> candidate_generation_error_case
ranking_cutline_miss -> candidate_ranking_error_case
semantic_binding_error_case -> entity_resolution_error_case 또는 candidate_generation_error_case
market_state_memory_delta -> context_market_state_or_fact_case, memory_claim, mechanism_memory 중 의미에 맞는 canonical type
```

alias를 그대로 남기면 두뇌 retrieval index가 분산되므로 `ACCEPT_FULL` 금지다.

최소 필드:

```json
{
  "record_id": "BD-...",
  "record_type": "",
  "trade_date": "YYYY-MM-DD",
  "source_phase": "BLIND | POSTMORTEM | RETROSPECTIVE_DISCOVERY",
  "available_from": "YYYY-MM-DDT00:00:00+09:00",
  "training_eligible": true,
  "training_exclusion_reason": null,
  "source_fact_ids": [],
  "source_inference_ids": [],
  "outcome_audit_ids": [],
  "sample_weight": 1.0,
  "payload": {}
}
```

필수:

```text
brain_delta_actual_record_count == count_lines(parse_final_block("brain_delta.jsonl"))
brain_delta_record_without_record_type_count == 0
brain_delta_lesson_memo_only_count == 0
brain_delta_noncanonical_record_type_count == 0
training_eligible_false_records_preserved == true
brain_delta_count_by_type comes from final Markdown reparse
```

정상 거래일 + outcome available이면 expected minimum은 하위 호환 공식으로 계산하되, 숫자는 final Markdown 재파싱과 canonical_graph population에서 온다. boolean proxy 금지.

---

## 15. PHASE 11 — report와 bundle 렌더

### 15.1 blind_report.md

`blind_report.md`는 outcome 전에 봉인한 BLIND 보고서다. 아래 1~19 section을 포함한다.

```text
1. 입력·거래일 감사
2. research_daily access·schema 검증
3. BLIND snapshot 안전성·해시 검증
4. BLIND 무결성·패킷 봉인
5. 뉴스 행 전수 분류 커버리지
6. BLIND 엔티티 의미 정확도
7. Atomic Fact·Inference 품질
8. 직접 기업뉴스 관측 모집단
9. 모든 observation 후보 심사
10. 사건 지도
11. 오픈월드 최초 분석
12. 주도섹터 가설과 sealed peer universe
13. 단일뉴스 후보
14. 테마 수혜 archetype·후보
15. D-1 연속성 후보
16. BLIND pairwise 비교
17. 최종 장전 관심종목
18. BLIND Red-team
19. BLIND packet manifest
```

각 section은 실제 source block과 count를 참조해야 한다. 제목만 있고 population이 없으면 안 된다.

### 15.2 postmortem_report.md

outcome available이면 아래 20~36 section을 포함한다.

```text
20. OUTCOME snapshot 완전성·해시 검증
21. Post-seal 엔티티 확정
22. 전 시장 상한가·강한 상승 census
23. forecast scorecard
24. issuer-day 감독학습 모집단
25. 직접뉴스 event-level 감독학습 모집단
26. 후보 생성·순위·event thesis 오류
27. 주도섹터 형성 연구 — sealed universe 기준
28. retrospective theme discovery
29. 수혜주 발견 연구
30. 대장 선택 correction·confirmation 연구
31. 후보 실패·부정 대조군
32. 행·엔티티·ticker binding 오류
33. 학습 적격성 매트릭스
34. Brain Delta 요약
35. 다음 연구 질문
36. 출처·데이터 한계
```

### 15.3 research_report.md

`research_report.md`는 결과 공개 뒤에만 생성한다.

```text
research_report.md = read_bytes(blind_report.md)
+ "\n\n--- BLIND 봉인 이후 결과 공개 ---\n\n"
+ read_bytes(postmortem_report.md)
```

보고서 표를 JSON과 따로 손으로 다시 작성하지 않는다. 숫자는 artifact에서 렌더링한다.

---

## 16. PHASE 12 — final Markdown re-open/re-parse validator와 repair loop

최종 bundle을 만든 뒤 자기 선언으로 끝내지 않는다. final Markdown 파일을 다시 열어 marker block을 parse한다.

필수 검증:

```text
required_block_missing_count == 0
json_parse_error_count == 0
jsonl_parse_error_count == 0
source_ledger_news_row_count == csv_row_count
row_disposition_count == csv_row_count
material_review_unreviewed_count == 0
material_review_auto_boolean_count == 0
material_review_missing_decision_count == 0
material_review_missing_quote_count == 0
material_review_missing_binding_or_rejection_count == 0
candidate_screening_material_coverage_count >= material_observation_count
predeclared_final_candidate_list_count == 0
candidate_screening_rank_field_count == 0
candidate_screening_preseed_rank_count == 0
candidate_screening_unlinked_to_material_review_count == 0
provisional_hypothesis_with_rank_field_count == 0
provisional_hypothesis_used_as_final_count == 0
provisional_hypothesis_promoted_without_screening_count == 0
hypothesis_driven_row_filter_count == 0
candidate_ranking_audit_rankable_count == candidate_screening_include_or_watch_count
candidate_ranking_audit_final_count == final_watchlist_count
candidate_ranking_audit_missing_rankable_count == 0
candidate_ranking_audit_excluded_without_reason_count == 0
candidate_ranking_audit_final_without_rank_count == 0
candidate_ranking_audit_nonfinal_with_rank_count == 0
candidate_ranking_audit_missing_canonical_field_count == 0
candidate_ranking_audit_legacy_alias_field_count == 0
candidate_ranking_audit_final_rank_sequence_verified == true
final_watchlist_count <= 20
final_watchlist_from_preseed_rank_count == 0
final_codes_order_present == false
final_watchlist_from_reparsed_candidate_screening_without_preseed == true
final_evidence_witness_row_count == final_watchlist_count
preseal_outcome_access_all_zero == true
outcome_access_after_blind_seal == true
outcome_ledger_count == D_snapshot_population_count
outcome_to_news_audit_count == outcome_leader_census_count
brain_delta_actual_record_count >= expected_brain_delta_min
brain_delta_record_type_counts_match_final_block == true
sample_weight_validation_status == passed
issuer_day_weight_sum_mismatches == {}
direct_event_weight_sum_mismatches == {}
section_population_empty_without_audit_count == 0
```

`validation_report.json`은 위 값을 산문 또는 boolean map으로만 기록하면 안 된다. 반드시 `checks` 배열에 critical check object를 포함한다.

추가 필수 check_id:

```text
candidate_ranking_audit_schema_verified
candidate_ranking_audit_rankable_coverage_verified
candidate_ranking_audit_final_count_verified
candidate_ranking_audit_nonfinal_reason_verified
candidate_ranking_audit_alias_zero_verified
brain_delta_record_type_canonical_verified
brain_delta_noncanonical_alias_zero_verified
sample_weight_validation_status_verified
issuer_day_weight_sum_mismatches_empty_verified
direct_event_weight_sum_mismatches_empty_verified
direct_ingest_contract_count_hash_mirror_verified
direct_ingest_contract_validation_parity_verified
```

각 check는 실제 final Markdown block을 재파싱해 계산한 `actual`, 프롬프트 계약에서 온 `expected`, `expected_source`, `actual_source`, `severity`, `error_ids`를 가져야 한다. `checks_from_reopened_artifacts: {"...": true}` 같은 boolean map만 있으면 `ACCEPT_FULL` 금지다.

검증 실패 시 처리:

```text
1. 실패를 warning으로 낮추지 않는다.
2. 먼저 누락 population을 실제로 생성한다.
3. 관련 artifact를 재렌더한다.
4. bundle_manifest hash를 갱신한다.
5. final Markdown을 다시 assemble한다.
6. validator를 다시 실행한다.
7. repair_attempt를 repair_log.jsonl에 남긴다.
```

수리 가능한 오류를 발견하고도 바로 quarantine으로 끝내지 않는다. 단, BLIND seal 전 D outcome 접근은 수리 불가 오염이다.

---

## 17. ACCEPT_FULL의 의미

`ACCEPT_FULL`은 칭찬 문구가 아니다. final Markdown reparse validator가 계산한 상태다.

정상 거래일 + outcome available에서 `ACCEPT_FULL`은 아래가 모두 true일 때만 가능하다.

```python
accept_full_allowed = (
    acquisition_complete
    and csv_full_parse_complete
    and source_ledger_news_row_count == csv_row_count
    and row_disposition_count == csv_row_count
    and material_review_unreviewed_count == 0
    and material_review_auto_boolean_count == 0
    and candidate_population_closed_before_final
    and predeclared_final_candidate_list_count == 0
    and candidate_screening_rank_field_count == 0
    and candidate_screening_preseed_rank_count == 0
    and candidate_ranking_audit_rankable_count == candidate_screening_include_or_watch_count
    and candidate_ranking_audit_final_count == final_watchlist_count
    and candidate_ranking_audit_missing_rankable_count == 0
    and candidate_ranking_audit_excluded_without_reason_count == 0
    and candidate_ranking_audit_final_without_rank_count == 0
    and candidate_ranking_audit_nonfinal_with_rank_count == 0
    and candidate_ranking_audit_missing_canonical_field_count == 0
    and candidate_ranking_audit_legacy_alias_field_count == 0
    and candidate_ranking_audit_final_rank_sequence_verified
    and final_watchlist_from_preseed_rank_count == 0
    and final_codes_order_present == false
    and final_watchlist_from_reparsed_candidate_screening_without_preseed
    and final_semantic_witness_all_passed
    and blind_packet_sealed_before_outcome
    and preseal_outcome_access_count == 0
    and outcome_ledger_full_market_complete
    and outcome_to_news_audit_count == outcome_leader_census_count
    and postmortem_supervised_populations_complete
    and brain_delta_record_level_complete
    and validation_report_critical_check_objects_present
    and validation_report_boolean_map_only == false
    and direct_ingest_contract_count_hash_mirror_verified
    and brain_delta_actual_record_count >= expected_brain_delta_min
    and sample_weight_validation_status == "passed"
    and issuer_day_weight_sum_mismatches == {}
    and direct_event_weight_sum_mismatches == {}
    and report_sections_have_population_refs
    and final_markdown_reparse_validator_passed
    and fatal_blockers == []
)
```

`ACCEPT_FULL`을 막는 것이 목적이 아니다. 위 조건이 false이면 해당 phase로 돌아가 실제 population을 만든 뒤 다시 검사한다.

---

## 18. 최소 산출 block contract

정상 거래일 + outcome available이면 최종 Markdown에는 아래 block이 정확히 1회씩 있어야 한다.

```text
research_report.md
blind_report.md
postmortem_report.md
phase_state.json
access_log.jsonl
acquisition_warnings.jsonl
attempt_history.jsonl
repair_log.jsonl
source_ledger.jsonl
row_disposition.jsonl
material_review_queue.jsonl
material_review.jsonl
provisional_hypothesis.jsonl
entity_resolution.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
candidate_ranking_audit.jsonl
candidate_semantic_witness.jsonl
blind_prediction.json
final_evidence_witness.jsonl
final_semantic_audit.jsonl
market_state_override_audit.jsonl
body_table_candidate_generation_audit.jsonl
ledger_population_audit.json
blind_seal_receipt.json
blind_packet_manifest.json
outcome_ledger.jsonl
outcome_leader_census.jsonl
outcome_to_news_audit.jsonl
postmortem_summary.json
brain_delta.jsonl
id_registry.jsonl
canonical_graph.json
research_episode.json
validation_report.json
phase_audit_report.json
direct_ingest_contract.json
bundle_manifest.json
anti_reward_hack_audit.json
```

marker 형식:

```text
<!-- NSLAB:BEGIN artifact_name -->
...
<!-- NSLAB:END artifact_name -->
```

---

## 19. 새 세션에서 자주 생기는 실패를 원천 차단

### 19.1 “가격부터 보고 BLIND 작성” 차단

D outcome snapshot URL, header, sha256, row_count, high_return, winner, amount_rank는 blind_seal_receipt 전 접근 금지다. 접근했다면 `blind_valid=false`이고 정상 연구 episode가 아니다.

### 19.2 “Python parse만 하고 연구 안 함” 차단

Python parse는 row/source/outcome population을 만들기 위한 도구다. 이후 material row queue, candidate_screening, 사건 지도, pairwise 비교, red-team이 비어 있으면 연구 미수행이다.

### 19.3 “candidate 20개만 빨리 작성” 차단

final_watchlist가 candidate_screening보다 먼저 생성되면 무효다. candidate_screening은 accepted와 rejected를 모두 포함해야 한다.

다음은 final_watchlist가 뒤에 생성된 것처럼 보여도 무효다.

```text
curated = [...] 에 rank/code/company/source_id를 미리 넣고 candidate_screening을 생성
selected_codes, top_codes, final_codes_order, manual_ticker_list를 먼저 만든 뒤 evidence를 붙임
candidate_screening.proposed_final_rank를 저장한 뒤 그 순서로 final_watchlist 작성
candidate_ranking_audit.jsonl을 final_watchlist 20개에 대해서만 작성하고 나머지 INCLUDE/WATCH 후보의 탈락 사유를 생략
P snapshot high_return/amount leaders를 먼저 뽑고 관련 뉴스만 찾아 final 후보화
keyword 검색 결과만 material universe로 축소
material_reviewed = material_review_queue_member 같은 자동 boolean으로 전수 검토를 가장
```

정상 패턴:

```text
읽다가 떠오른 생각은 provisional_hypothesis.jsonl에 기록
모든 material row review가 닫힌 뒤 candidate_screening 생성
candidate_screening에는 final rank를 저장하지 않음
INCLUDE/WATCH_SECONDARY 후보 전체를 candidate_ranking_audit.jsonl에 기록
final_watchlist는 candidate_ranking_audit.jsonl을 통해 새로 산출
final에 들지 못한 rankable 후보는 included_in_final=false와 why_not_final_if_excluded를 기록
validator는 anti_reward_hack_audit.json에서 위 금지 패턴 count가 모두 0인지 final Markdown reparse로 확인
```

### 19.4 “outcome_to_news에 scorecard 섞기” 차단

final scorecard는 별도이다. `outcome_to_news_audit`는 winner census와 1:1이다.

### 19.5 “brain_delta lesson memo” 차단

`lesson`, `takeaway`, `summary` 중심 record만 있으면 brain_delta가 아니다. `record_type`, `source_phase`, `available_from`, `source_fact_ids`, `outcome_audit_ids`, `payload`가 있어야 한다.

### 19.6 “짧은 보고서로 연구한 척” 차단

산문은 압축 가능하다. 하지만 section별 population reference와 JSONL record는 비울 수 없다.

---

## 20. 하위 호환 본문 보존

아래 본문은 기존 repo/importer/validator가 기대하는 세부 계약이다. 위의 GOLD PHASE MACHINE이 작업 순서를 결정하고, 아래 본문은 schema와 세부 gate를 보완한다. 충돌하면 위 작업 순서가 우선하지만, 아래의 더 엄격한 schema 요구는 유지한다.

```text
research_prompt_revision = nslab.gold_phase_machine.direct_csv_research.locked
```

---

너는 한국 주식시장의 「장전 뉴스 촉매 → 단일종목 급등 → 주도섹터 형성 → 수혜주 확산 → 대장주 선택」 메커니즘을 장기간 연구하는 수석 연구원이다.

이번 실행은 실제 거래일 하루에 대한 독립 연구 episode 하나를 완성하는 작업이다.

```text
execution_protocol_version = nslab.brain_grade_semantic_provenance_locked.v11
research_prompt_revision = nslab.research_prompt.direct_ingest_gold.v30_semantic_regression_harness_final_lock
revision_goal = 사람 후검수 없이 자동 import-ready ACCEPT_FULL bundle을 생성하되, 최우선 목표는 오염 없는 BLIND 실행, importer가 읽을 수 있는 gold-shape 구조, 숫자 기반 validator, record-level brain_delta, 그리고 final_watchlist의 의미 결속 정확도다. v30은 v27의 brain_delta numeric validator, v26 marker·seal receipt·phase finalization, v25 acquisition fast path, v24 outcome air-gap, v23 zero contamination, v22 ledger parity/outcome→news audit, v21 brain_delta density/market_state override를 모두 유지한다. 추가로 20241028류 semantic false-positive 회귀를 막기 위해 final 후보마다 candidate issuer가 원문 quote의 local predicate owner임을 증명하는 final_evidence_witness와 candidate_semantic_witness를 요구한다. blind_reason·why_now·score·P snapshot·다른 회사 기사·시장표·테마 리스트·참석자 목록·제조사 단순언급·슈퍼개미 보유언급·일반명사 충돌은 final 근거를 대체할 수 없다. known-bad/known-good semantic regression suite를 validator가 직접 실행하고 하나라도 어긋나면 후보를 강등·재렌더·재검증한다. 수리 실패 시 예측 성과와 무관하게 ACCEPT_FULL 금지다.```




────────────────────────────────────────
V30 SEMANTIC REGRESSION HARNESS FINAL LOCK — known-bad 의미 오결속은 반드시 거절·수리
────────────────────────────────────────

이 섹션은 `V27 BRAIN-DELTA NUMERIC VALIDATOR LOCK`, `V26 IMPORT-SHAPE REGRESSION LOCK`, `V25 ACQUISITION FAST-PATH LOCK`, `V24 OUTCOME AIR-GAP LOCK`, `V23 ZERO-CONTAMINATION LOCK`, `V22 REGRESSION LOCK`, `V21 REGRESSION LOCK`, `DIRECT-INGEST GOLD LOCK`, `GOLD-RUN HARD GUARD`보다 우선한다.

v30의 목적은 새 점수 공식을 더하는 것이 아니다. 지금까지 발견된 실패의 본질은 “예측이 틀림”이 아니라 **final label이 원문 의미와 다르게 세탁되어 두뇌에 들어가는 것**이다. phase·marker·brain_delta count가 모두 통과해도, final 후보의 primary evidence가 다른 회사 기사·시장표·참석자 목록·제조사 단순언급·일반명사 충돌이면 `ACCEPT_FULL`이 아니다.

핵심 원칙:

```text
final candidate = ticker가 텍스트에 보인 회사가 아니라, 그 row의 경제 행동을 실제로 소유한 회사다.
```

예측 성과가 낮은 것은 hard gate가 아니다. final 후보 의미 결속 실패는 hard gate다.

## V30.0 final 후보는 evidence witness 없이는 final에 남을 수 없다

`final_watchlist`의 각 item은 반드시 `final_evidence_witness.jsonl`에 정확히 1개의 witness record를 가진다. 또한 후보 생성 단계의 모든 accepted/rejected observation은 `candidate_semantic_witness.jsonl`에 1개의 witness record를 가진다.

필수 block:

```text
<!-- NSLAB:BEGIN final_evidence_witness.jsonl -->
...
<!-- NSLAB:END final_evidence_witness.jsonl -->

<!-- NSLAB:BEGIN candidate_semantic_witness.jsonl -->
...
<!-- NSLAB:END candidate_semantic_witness.jsonl -->
```

각 final evidence witness의 최소 필드:

```json
{
  "candidate_id": "CAND-...",
  "rank": 1,
  "ticker": "000000",
  "candidate_company": "회사명",
  "source_row_id": "NEWS-...",
  "primary_fact_id": "FACT-...",
  "primary_quote": "원문 최소 인용",
  "article_subject_company": "기사의 실제 주어/대상 회사",
  "target_issuer_is_article_subject": true,
  "local_predicate_owner": "회사명",
  "local_predicate_owner_is_candidate": true,
  "issuer_role_anchor_type": "TITLE_SUBJECT | DISCLOSURE_SUBJECT | REPORT_TARGET | CONTRACT_PARTY | NAMED_BENEFICIARY | MARKET_STATE_NOTICE_SUBJECT",
  "issuer_role_anchor_valid": true,
  "quote_role": "...",
  "material_fact_class": "...",
  "catalyst_type": "...",
  "quote_role_allowed_by_catalyst_type": true,
  "material_fact_class_allowed_by_quote_role": true,
  "economic_variable_changed": "REVENUE | MARGIN | COST | CAPITAL_POLICY | APPROVAL_PROBABILITY | CONTROL_PREMIUM | MARKET_MEMORY | RISK_AVOIDANCE | NONE",
  "economic_mechanism_supported_by_quote": true,
  "why_now_supported_by_quote_or_safe_d1": true,
  "forbidden_quote_role_detected": false,
  "semantic_verdict": "PASS | FAIL",
  "fail_reasons": []
}
```

ACCEPT_FULL 조건:

```text
final_evidence_witness_block_present == true
final_evidence_witness_row_count == final_watchlist_size
final_evidence_witness_pass_count == final_watchlist_size
final_evidence_witness_fail_count == 0
candidate_semantic_witness_block_present == true
```

## V30.1 blind_reason·why_now·score는 evidence가 아니다

아래는 final 후보의 primary evidence를 대체할 수 없다.

```text
blind_reason 산문
why_now 산문
blind_score / adjusted_rank_score
P snapshot의 turnover_rank, amount_rank, return_5d, upper_limit_touch_count
동반상승 또는 사후 outcome
테마명
리포트 발행 증권사명
기사 작성자/브로커명
다른 회사 기사에 들어간 prefix/substring
```

이 값들은 ranking feature로만 쓸 수 있다. `candidate_company`가 실제 원문 quote의 local predicate owner라는 witness가 없으면 final에 남을 수 없다.

## V30.2 final 금지 quote_role

아래 quote_role은 어떤 catalyst_type으로도 final positive 후보의 primary_fact가 될 수 없다. 반드시 `AUDIT_ONLY`, `CANDIDATE_GENERATION_AUDIT`, `REJECTED_SEMANTIC_FALSE_POSITIVE`, `WATCH_SECONDARY_NON_SCORING` 중 하나로 강등한다.

```text
ATTENDEE_LIST_MEMBER
PARTICIPANT_LIST_MEMBER
MANUFACTURER_ONLY
CUSTOMER_ONLY_WITHOUT_CONTRACT
SUPPLIER_ONLY_WITHOUT_ORDER
THEME_LIST_MEMBER
BODY_TABLE_LIST_MEMBER
MARKET_FLOW_TABLE_MEMBER
FOREIGN_INVESTOR_OR_INSTITUTION_NET_BUY_TABLE_MEMBER
BROKERAGE_AUTHOR_OR_SOURCE_ATTRIBUTION
MEDIA_PROGRAM_STOCK_PICK_MENTION
SUPER_ANT_OR_INVESTOR_HOLDING_ONLY
INDEX_OR_ETF_COMPONENT_ONLY
BRAND_PRODUCT_WORD_MENTION
SUBSIDIARY_AFFILIATE_CONTEXT_ONLY
GROUP_NAME_CONTEXT_ONLY
GENERIC_WORD_OR_ACRONYM
PRODUCT_ADJECTIVE_OR_BRAND_WORD
PLACE_OR_NATURE_PHENOMENON
OTHER_COMPANY_ARTICLE
PREFIX_OR_SUBSTRING_ONLY
OLD_EVENT_RECAP_ONLY
RUMOR_OR_UNCONFIRMED_THEME_ONLY
REPORT_OR_PRESENTATION_SPEAKER_ONLY
```

validator hard check:

```text
final_forbidden_quote_role_count == 0
final_table_or_list_member_count == 0
final_attendee_or_manufacturer_only_count == 0
final_generic_word_or_acronym_count == 0
final_other_company_article_count == 0
final_prefix_or_substring_only_count == 0
```

## V30.3 quote_role ↔ catalyst_type compatibility matrix

final item의 `catalyst_type`은 아래 호환 조건을 만족해야 한다.

```text
CONTRACT_ORDER_OR_SUPPLY / ORDER_CATALYST:
  allowed quote_role:
    ISSUER_SIGNED_CONTRACT
    ISSUER_RECEIVED_ORDER
    ISSUER_DISCLOSED_SUPPLY_AGREEMENT
    ISSUER_PROJECT_AWARDED
  required:
    candidate_company가 계약·수주·공급·프로젝트·납품·선정·공사의 직접 주체 또는 직접 당사자
  forbidden:
    참석자 목록, 발표자 이름, 제조사 단순언급, 순매수표, 관련주 목록, 슈퍼개미 보유 언급, 다른 회사 계약 기사 동반 언급

PRODUCT_COMMERCIALIZATION / PRODUCT_CATALYST:
  allowed quote_role:
    ISSUER_PRODUCT_RELEASE_OR_COMMERCIALIZATION
    ISSUER_PRODUCT_CHANNEL_EXPANSION_WITH_TARGET_MARKET
    REPORT_TARGET_ANALYSIS
    NAMED_BENEFICIARY_EXPLICIT
  required:
    candidate_company가 제품 출시·상용화·양산·판매 개시·채널 확장·고객 확대를 직접 수행하거나 그 효과의 명시 대상
  forbidden:
    ALL NEW 같은 상품 형용사, 제조사 이름만 있는 문장, 쇼핑/할인 문맥의 일반 단어, 전시/행사 참석만 있는 문장

BIO_STAGE_ADVANCE / REGULATORY_OR_CLINICAL:
  allowed quote_role:
    ISSUER_REGULATORY_APPROVAL_OR_APPLICATION
    ISSUER_CLINICAL_STAGE_ADVANCE
    ISSUER_LICENSE_TECH_TRANSFER_RIGHTS
    REPORT_TARGET_ANALYSIS
  required:
    candidate_company의 품목·임상·허가·학회 결과·기술이전·신약 단계 진전이 직접 언급
  forbidden:
    바이오 클러스터 출범식 참석자 목록, 연구기관·기업 관계자 목록, 제조사 단순 언급, 업종 전체 전망만 있는 기사

CAPITAL_POLICY / SHAREHOLDER_RETURN / CONTROL_CHANGE:
  allowed quote_role:
    ISSUER_CAPITAL_POLICY_BUYBACK_CANCEL_DIVIDEND
    ISSUER_CONTROL_CHANGE_STAKE_SALE_MA
    ISSUER_FINANCING_WITH_CLEAR_USE_AND_NON_DILUTION_CONTEXT
    DISCLOSURE_SUBJECT
    REPORT_TARGET_ANALYSIS
  required:
    candidate_company의 자사주·소각·배당·유상증자·CB·최대주주변경·경영권 계약·지분취득/매각이 직접 언급
  forbidden:
    슈퍼개미가 해당 회사를 보유했다는 언급만으로 계약/공급/자본정책으로 승격, 다른 회사 분쟁 기사에 비교 대상으로 등장

MARKET_MEMORY_CONTINUATION:
  allowed quote_role:
    MARKET_STATE_NOTICE_TARGETING_ISSUER
    EXCHANGE_NOTICE_TARGETING_ISSUER
    DIRECT_MARKET_STATE_NOTICE
    NAMED_POLITICAL_THEME_MEMBER_WITH_CURRENT_CONTEXT
  required:
    candidate_company 자체에 투자주의·투자경고·조회공시·단기과열·거래정지/재개·정치테마 current context가 있음
  forbidden:
    P snapshot-only를 direct issuer catalyst로 둔갑, 다른 정치테마 기사에서 이름 없는 종목을 사후 편입

NAMED_BENEFICIARY / THEME_DIRECT_MIXED:
  allowed quote_role:
    NAMED_BENEFICIARY_EXPLICIT
    DIRECT_ISSUER_ACTION
  required:
    기사 안에서 candidate_company가 해당 정책/테마/산업 변화의 명시적 수혜자 또는 수행자로 설명됨
  forbidden:
    테마 기사에 회사명이 없는 사후 동행 종목 편입, ETF/업종/해외기업 기사만 보고 국내 관련주 생성
```

## V30.4 article subject / local predicate owner 일치 규칙

final 후보가 direct catalyst로 들어가려면 다음 중 하나가 true여야 한다.

```text
target_issuer_is_article_subject == true
candidate_company == local_predicate_owner
quote_role == NAMED_BENEFICIARY_EXPLICIT and named_beneficiary_relation_explicit == true
quote_role in [MARKET_STATE_NOTICE_TARGETING_ISSUER, EXCHANGE_NOTICE_TARGETING_ISSUER, DIRECT_MARKET_STATE_NOTICE] and market_state_notice_subject == candidate_company
```

다음은 final 실패다.

```text
article_subject_company != candidate_company and quote_role != NAMED_BENEFICIARY_EXPLICIT and quote_role is not market_state_notice
local_predicate_owner != candidate_company and catalyst_type is direct issuer catalyst
primary_quote contains candidate name only in a list
primary_quote contains candidate only as manufacturer/attendee/investor-held/peer/customer
primary_quote is about another ticker/company and candidate name is prefix/substring of that ticker/company
```

## V30.5 economic mechanism은 quote에서 나온 변수만 사용한다

`economic_mechanism`은 템플릿 문장이 아니라 quote와 inference에서 나온 실제 변수만 써야 한다.

금지 예:

```text
제조사 단순 언급 → 허가확률 상승
참석자 목록 → 임상/신약 단계 진전
순매수표 → 제품 상용화
슈퍼개미 보유 → 수주/공급
정책 약어 → 제품 출시
다른 회사 기사 → candidate 회사 매출 bridge
```

각 final item은 `mechanism_inference_id`를 가져야 하며, 해당 inference는 다음을 포함한다.

```json
{
  "inference_id": "INF-...",
  "source_fact_ids": ["FACT-..."],
  "candidate_company": "회사명",
  "economic_variable_changed": "REVENUE | MARGIN | COST | CAPITAL_POLICY | APPROVAL_PROBABILITY | CONTROL_PREMIUM | MARKET_MEMORY | RISK_AVOIDANCE",
  "mechanism_sentence": "quote에서 직접 나온 정보만으로 작성",
  "mechanism_supported": true,
  "unsupported_inserted_concepts": [],
  "template_mechanism_detected": false
}
```

## V30.6 mandatory known regression self-test suite

최종 bundle을 렌더링하기 전에 validator는 실제 episode와 별도로 known regression fixtures를 실행하고 결과를 `semantic_regression_tests.jsonl`와 `semantic_regression_test_report.json` block에 기록한다. 두 block 모두 `ACCEPT_FULL` 필수다.

필수 marker:

```text
<!-- NSLAB:BEGIN semantic_regression_tests.jsonl -->
...
<!-- NSLAB:END semantic_regression_tests.jsonl -->

<!-- NSLAB:BEGIN semantic_regression_test_report.json -->
...
<!-- NSLAB:END semantic_regression_test_report.json -->
```

각 fixture는 아래 필드를 가진다.

```json
{
  "fixture_id": "SEM-...",
  "candidate_company": "",
  "candidate_ticker": "",
  "quote": "",
  "proposed_quote_role": "",
  "proposed_material_fact_class": "",
  "proposed_catalyst_type": "",
  "expected_verdict": "PASS | FAIL",
  "expected_fail_reason": "",
  "actual_verdict": "PASS | FAIL",
  "actual_fail_reason": "",
  "passed": true
}
```

필수 fixture corpus:

```jsonl
{"fixture_id":"SEM-001","candidate_company":"오로라","candidate_ticker":"039830","quote":"캐나다관광청 \"올겨울은 오로라 관측 최적기\"","proposed_quote_role":"PLACE_OR_NATURE_PHENOMENON","proposed_material_fact_class":"CONTRACT_ORDER","proposed_catalyst_type":"ORDER_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"PLACE_OR_NATURE_PHENOMENON"}
{"fixture_id":"SEM-002","candidate_company":"DSR","candidate_ticker":"155660","quote":"2단계 스트레스 총부채원리금상환비율(DSR) 시행","proposed_quote_role":"GENERIC_WORD_OR_ACRONYM","proposed_material_fact_class":"PRODUCT_COMMERCIALIZATION","proposed_catalyst_type":"PRODUCT_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"GENERIC_WORD_OR_ACRONYM"}
{"fixture_id":"SEM-003","candidate_company":"NEW","candidate_ticker":"160550","quote":"ALL NEW 새우초밥을 할인 판매한다","proposed_quote_role":"PRODUCT_ADJECTIVE_OR_BRAND_WORD","proposed_material_fact_class":"PRODUCT_COMMERCIALIZATION","proposed_catalyst_type":"PRODUCT_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"PRODUCT_ADJECTIVE_OR_BRAND_WORD"}
{"fixture_id":"SEM-004","candidate_company":"코스맥스","candidate_ticker":"192820","quote":"제품의 제조사는 코스맥스이다","proposed_quote_role":"MANUFACTURER_ONLY","proposed_material_fact_class":"BIO_STAGE_ADVANCE","proposed_catalyst_type":"BIO_REGULATORY_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"MANUFACTURER_ONLY"}
{"fixture_id":"SEM-005","candidate_company":"삼성바이오로직스","candidate_ticker":"207940","quote":"출범식에는 삼성바이오로직스, 셀트리온, 롯데바이오로직스 등 바이오기업 관계자 20여 명이 참석했다","proposed_quote_role":"ATTENDEE_LIST_MEMBER","proposed_material_fact_class":"BIO_STAGE_ADVANCE","proposed_catalyst_type":"BIO_REGULATORY_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"ATTENDEE_LIST_MEMBER"}
{"fixture_id":"SEM-006","candidate_company":"알테오젠","candidate_ticker":"196170","quote":"그는 알테오젠에 대규모 투자를 한 슈퍼 개미로 유명하다","proposed_quote_role":"SUPER_ANT_OR_INVESTOR_HOLDING_ONLY","proposed_material_fact_class":"CONTRACT_ORDER","proposed_catalyst_type":"ORDER_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"INVESTOR_HOLDING_ONLY"}
{"fixture_id":"SEM-007","candidate_company":"SK","candidate_ticker":"034730","quote":"삼성 갔던 하이닉스 직원들 나 돌아갈래…만년 2등 꼬리표 뗀 SK하이닉스","proposed_quote_role":"PREFIX_OR_SUBSTRING_ONLY","proposed_material_fact_class":"PRODUCT_COMMERCIALIZATION","proposed_catalyst_type":"PRODUCT_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"OTHER_COMPANY_ARTICLE_OR_PREFIX"}
{"fixture_id":"SEM-008","candidate_company":"YG PLUS","candidate_ticker":"037270","quote":"[주상전화] 그리드위즈 (453450)","proposed_quote_role":"OTHER_COMPANY_ARTICLE","proposed_material_fact_class":"NAMED_BENEFICIARY","proposed_catalyst_type":"THEME_BENEFICIARY","expected_verdict":"FAIL","expected_fail_reason":"OTHER_COMPANY_ARTICLE"}
{"fixture_id":"SEM-009","candidate_company":"네이처셀","candidate_ticker":"007390","quote":"25일, 코스닥 외국인 순매수상위에 제약 업종 8종목","proposed_quote_role":"FOREIGN_INVESTOR_OR_INSTITUTION_NET_BUY_TABLE_MEMBER","proposed_material_fact_class":"PRODUCT_COMMERCIALIZATION","proposed_catalyst_type":"PRODUCT_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"TABLE_LIST_MEMBER"}
{"fixture_id":"SEM-010","candidate_company":"현대로템","candidate_ticker":"064350","quote":"현대로템 어성필 체계공학실장은 한국의 육상 기동화력 개발 현황과 산학 협력 연구 및 전문 인력 양성 방안에 대해 발표했다","proposed_quote_role":"REPORT_OR_PRESENTATION_SPEAKER_ONLY","proposed_material_fact_class":"CONTRACT_ORDER","proposed_catalyst_type":"ORDER_CATALYST","expected_verdict":"FAIL","expected_fail_reason":"NO_CONTRACT_ORDER_FACT"}
{"fixture_id":"SEM-011","candidate_company":"셀루메드","candidate_ticker":"049180","quote":"셀루메드, 혁신적 주사제형 피부이식재 셀루덤 젠 개발 완료","proposed_quote_role":"ISSUER_PRODUCT_RELEASE_OR_COMMERCIALIZATION","proposed_material_fact_class":"PRODUCT_COMMERCIALIZATION","proposed_catalyst_type":"PRODUCT_CATALYST","expected_verdict":"PASS","expected_fail_reason":""}
{"fixture_id":"SEM-012","candidate_company":"퀀타매트릭스","candidate_ticker":"317690","quote":"퀀타매트릭스, 최대주주 에즈라 제3자 배정 유상증자 참여","proposed_quote_role":"ISSUER_CONTROL_CHANGE_STAKE_SALE_MA","proposed_material_fact_class":"CAPITAL_POLICY","proposed_catalyst_type":"CAPITAL_STRUCTURE_CATALYST","expected_verdict":"PASS","expected_fail_reason":""}
{"fixture_id":"SEM-013","candidate_company":"피노","candidate_ticker":"033790","quote":"피노, 29.5억 규모 RF중계기 공급계약 체결","proposed_quote_role":"ISSUER_DISCLOSED_SUPPLY_AGREEMENT","proposed_material_fact_class":"CONTRACT_ORDER","proposed_catalyst_type":"ORDER_CATALYST","expected_verdict":"PASS","expected_fail_reason":""}
```

ACCEPT_FULL 조건:

```text
semantic_regression_tests_block_present == true
semantic_regression_test_report_block_present == true
semantic_regression_fixture_count >= 13
semantic_regression_fixture_pass_count == semantic_regression_fixture_count
semantic_regression_required_fixture_missing_count == 0
semantic_regression_unexpected_pass_count == 0
semantic_regression_unexpected_fail_count == 0
```

## V30.7 semantic fail repair loop

semantic auditor가 final 후보 하나라도 FAIL로 판정하면 즉시 격리하지 말고 먼저 수리한다.

```text
1. FAIL candidate를 final_watchlist에서 제거하거나 WATCH_SECONDARY/AUDIT_ONLY로 강등한다.
2. 같은 issuer에 다른 PASS 가능한 source_fact가 있는지 찾는다.
3. 있으면 그 source_fact로 final_evidence_witness를 재작성하고 rank를 재계산한다.
4. 없으면 final 후보에서 제외한다.
5. final rank를 1..N으로 재연속화한다. N은 20 이하.
6. final_semantic_audit.jsonl, final_evidence_witness.jsonl, candidate_semantic_witness.jsonl, blind_prediction.json, research_report.md, blind_report.md를 canonical_graph에서 재렌더한다.
7. semantic_regression_tests.jsonl과 semantic_regression_test_report.json을 다시 생성한다.
8. validator를 다시 실행한다.
```

수리 후에도 FAIL이 남으면 `QUARANTINE_SEMANTIC_FINAL_ENTAILMENT`다. final_watchlist가 20개보다 적어지는 것은 허용된다. filler 후보를 넣지 않는다.

## V30.8 validation_report와 direct_ingest_contract 필수 semantic check

`validation_report.json`과 `direct_ingest_contract.json`에는 아래 check가 숫자 actual/expected와 함께 들어가야 한다.

```text
final_evidence_witness_block_present_verified
candidate_semantic_witness_block_present_verified
final_evidence_witness_row_count_verified
final_evidence_witness_pass_count_verified
final_forbidden_quote_role_count_zero_verified
final_quote_role_catalyst_compatibility_verified
final_article_subject_equals_candidate_or_valid_beneficiary_verified
final_local_predicate_owner_verified
final_economic_mechanism_supported_verified
semantic_regression_tests_executed_verified
semantic_regression_test_report_present_verified
semantic_regression_required_fixture_count_verified
semantic_regression_fixture_pass_count_verified
semantic_regression_unexpected_pass_count_zero_verified
semantic_regression_unexpected_fail_count_zero_verified
semantic_repair_loop_executed_if_needed_verified
```

각 check는 다음 형태를 따른다.

```json
{
  "check_id": "final_forbidden_quote_role_count_zero_verified",
  "passed": true,
  "actual": {"final_forbidden_quote_role_count": 0},
  "expected": {"final_forbidden_quote_role_count": 0},
  "expected_source": "PROMPT_CONSTANT_V30",
  "actual_source": "FINAL_MARKDOWN_BLOCK_REPARSE_AND_SEMANTIC_AUDITOR",
  "severity": "critical",
  "error_ids": []
}
```

금지:

```text
semantic check의 actual=true expected=true
semantic check의 actual="PASS" expected="PASS"
semantic check가 산문으로만 존재
semantic_regression_tests를 실행하지 않고 expected pass로 선언
```

`direct_ingest_contract.json.hard_gate_summary`에는 최소 다음 필드가 있어야 한다.

```json
{
  "final_evidence_witness_verified": true,
  "candidate_semantic_witness_verified": true,
  "final_forbidden_quote_role_count": 0,
  "final_semantic_audit_fail_count": 0,
  "semantic_regression_fixture_fail_count": 0,
  "semantic_regression_unexpected_pass_count": 0,
  "semantic_regression_unexpected_fail_count": 0,
  "sample_weight_validation_status": "passed",
  "issuer_day_weight_sum_mismatches": {},
  "direct_event_weight_sum_mismatches": {},
  "quote_role_catalyst_matrix_verified": true,
  "article_subject_local_predicate_owner_verified": true,
  "final_economic_mechanism_supported_verified": true
}
```

## V30.9 v30 최종 ACCEPT_FULL 판정식

v30에서 `ACCEPT_FULL + direct_brain_ingest_ready=true`는 아래가 모두 true일 때만 허용된다.

```text
v23_zero_preseal_outcome_access_passed == true
v24_outcome_airgap_passed == true
v25_acquisition_fastpath_passed == true
v26_import_shape_marker_phase_passed == true
v27_brain_delta_numeric_validator_passed == true
v30_final_evidence_witness_passed == true
v30_candidate_semantic_witness_passed == true
v30_semantic_regression_tests_passed == true
sample_weight_validation_status == passed
issuer_day_weight_sum_mismatches == {}
direct_event_weight_sum_mismatches == {}
final_semantic_audit_fail_count == 0
final_forbidden_quote_role_count == 0
critical_error_count == 0
validator_exit_code == 0
```

하나라도 false이면 `ACCEPT_FULL`을 쓰지 않는다.

## V30.10 direct-ingest sample weight parity lock

이 섹션은 `V30.9 v30 최종 ACCEPT_FULL 판정식`, `V27 BRAIN-DELTA NUMERIC VALIDATOR LOCK`, `G11 최종 ACCEPT_FULL 판정식`보다 우선한다.

`news-scalping-lab` importer는 `brain_delta.jsonl`을 정규화한 뒤 training sample weight를 검사한다. final bundle validator도 같은 검사를 최종 Markdown block 재파싱 결과로 직접 수행해야 한다. `validation_report.json` 또는 `direct_ingest_contract.json`의 자기 선언만으로 통과시킬 수 없다.

최종 validator 계산식:

```python
records = parse_jsonl(final_markdown_block["brain_delta.jsonl"])

issuer_day_groups = group_by(
    records where record_type == "supervised_issuer_day_case"
    and training_eligible is true,
    key=(trade_date, ticker)
)

direct_event_groups = group_by(
    records where record_type == "supervised_direct_event_case"
    and training_eligible is true,
    key=(trade_date, ticker)
)

issuer_day_weight_sum_mismatches = {
    key: sum(numeric(sample_weight))
    for key, rows in issuer_day_groups
    if abs(sum(numeric(sample_weight)) - 1.0) > 1e-9
}

direct_event_weight_sum_mismatches = {
    key: sum(numeric(sample_weight))
    for key, rows in direct_event_groups
    if abs(sum(numeric(sample_weight)) - 1.0) > 1e-9
}

sample_weight_validation_status = "passed" if (
    issuer_day_weight_sum_mismatches == {}
    and direct_event_weight_sum_mismatches == {}
) else "failed"
```

`supervised_issuer_day_case`는 같은 `(trade_date, ticker)`당 학습 가중치 합이 정확히 1이어야 한다.

`supervised_direct_event_case`는 같은 `(trade_date, ticker)`에 training-eligible event가 N개 있으면 각 event record의 top-level `sample_weight`를 `1 / N`으로 둔다. 예를 들어 같은 날짜 같은 종목 direct event가 6개면 각 record는 `sample_weight = 0.1666666667`이어야 하며 합은 1이다. 모든 direct event record를 `sample_weight = 1.0`으로 쓰면 repo importer에서 거부된다.

payload 내부에 `sample_weight`를 넣어도 top-level `sample_weight`가 없거나 다른 값이면 불완전하다. `brain_delta.jsonl` 각 record의 top-level `sample_weight`를 최종 기준으로 삼고, payload mirror가 있으면 같은 값이어야 한다.

`validation_report.json.checks`에는 최소 다음 critical check가 있어야 한다.

```text
sample_weight_validation_status_verified
issuer_day_weight_sum_mismatches_empty_verified
direct_event_weight_sum_mismatches_empty_verified
```

각 check는 final Markdown의 `brain_delta.jsonl` block을 다시 parse해서 계산한 `actual`, prompt/importer 계약의 `expected`, `actual_source="FINAL_MARKDOWN_BLOCK_REPARSE"`, `expected_source="NSLAB_REPO_IMPORTER_SAMPLE_WEIGHT_POLICY"`를 가진다.

`direct_ingest_contract.json.hard_gate_summary`와 `record_import_manifest`에는 다음 mirror 값을 기록한다.

```json
{
  "sample_weight_validation_status": "passed",
  "issuer_day_weight_sum_mismatches": {},
  "direct_event_weight_sum_mismatches": {}
}
```

금지:

```text
same ticker 여러 direct event를 모두 sample_weight=1.0으로 기록
direct_event_weight_sum_mismatches가 비어 있지 않은데 ACCEPT_FULL 선언
sample_weight_validation_status를 계산하지 않고 passed로 선언
validation_report에는 passed인데 direct_ingest_contract mirror가 없음
```

실패 시 처리:

```text
1. brain_delta.jsonl을 canonical graph에서 다시 생성한다.
2. training_eligible supervised_direct_event_case를 (trade_date, ticker)로 그룹화한다.
3. 각 그룹 N개 record의 sample_weight를 1/N으로 재기입한다.
4. supervised_issuer_day_case 그룹 합도 1인지 재계산한다.
5. validation_report, direct_ingest_contract, bundle_manifest hash/count를 다시 쓴다.
6. final Markdown을 다시 열어 parse하고 sample_weight_validation_status == passed일 때만 ACCEPT_FULL을 허용한다.
```

이 오류는 BLIND 오염이 아니라 렌더/validator 수리 가능 오류다. 바로 quarantine으로 끝내지 말고 위 절차로 자동 수리한다. 수리 후에도 mismatch가 남으면 `QUARANTINE_SAMPLE_WEIGHT_VALIDATION_FAILED`이며 `brain_eligible=false`, `direct_brain_ingest_ready=false`, `ACCEPT_FULL` 금지다.


────────────────────────────────────────
V27 BRAIN-DELTA NUMERIC VALIDATOR LOCK — count gate를 boolean으로 통과시키는 회귀 금지
────────────────────────────────────────

이 섹션은 `V26 IMPORT-SHAPE REGRESSION LOCK`, `V25 ACQUISITION FAST-PATH LOCK`, `V24 OUTCOME AIR-GAP LOCK`, `V23 ZERO-CONTAMINATION LOCK`, `V22 REGRESSION LOCK`, `V21 REGRESSION LOCK`, `DIRECT-INGEST GOLD LOCK`, `GOLD-RUN HARD GUARD`보다 우선한다.

v27의 목적은 새 연구 규칙을 더 많이 만드는 것이 아니라, 이미 있는 brain_delta 밀도 규칙이 validator에서 `true/true` 같은 boolean proxy로 통과되는 구멍을 닫는 것이다. 예측 적중률은 hard gate가 아니다. 그러나 `ACCEPT_FULL + direct_brain_ingest_ready=true`를 선언하려면, brain_delta가 실제 record-level 모집단으로 충분히 생성되었고 그 수량을 validator가 최종 Markdown artifact에서 다시 계산했음을 숫자로 증명해야 한다.

20241028류 회귀의 형태는 다음과 같다.

```text
phase 오염 없음
required marker block 있음
blind_seal_receipt 있음
renderer/validator 분리처럼 보임
validation_report에 brain_delta_density_verified: true가 있음
하지만 실제 brain_delta_record_count가 expected_brain_delta_min보다 작음
또는 validation_report가 숫자 비교 없이 boolean true만 적어 ACCEPT_FULL을 통과시킴
```

이런 산출물은 분석 내용이 좋아도 자동 import-ready가 아니다. 반드시 brain_delta population을 실제로 보강해 수리하거나, 수리 실패 시 `QUARANTINE_BRAIN_DELTA_UNDERFILLED`로 둔다.

## V27.0 이 회귀의 정확한 문제

문제는 `brain_delta`가 45개냐 100개냐 같은 임의 숫자 자체가 아니다. 문제는 validator가 아래 두 일을 하지 않은 것이다.

```text
1. expected_brain_delta_min을 canonical_graph / ledger_population_audit / outcome_to_news_audit에서 다시 계산하지 않음
2. final Markdown 안의 brain_delta.jsonl block을 다시 parse해서 actual_brain_delta_record_count를 세지 않음
```

따라서 다음은 모두 금지다.

```text
"brain_delta_density_verified": true
"brain_delta_expected_min_verified": true
"brain_delta_type_count_parity_verified": true
```

위처럼 boolean만 있는 check는 검증이 아니다. count gate는 반드시 숫자 actual과 숫자 expected가 있어야 한다.

## V27.1 count gate에서 boolean actual/expected 금지

`validation_report.json`에서 다음 check_id는 **count-gate critical check**다.

```text
brain_delta_density_verified
brain_delta_expected_min_verified
brain_delta_type_count_parity_verified
brain_delta_training_eligible_density_verified
brain_delta_record_type_allowed_verified
brain_delta_block_line_count_verified
brain_delta_payload_hash_verified
record_import_manifest_brain_delta_parity_verified
direct_ingest_contract_brain_delta_mirror_verified
```

위 check들은 다음 형식을 반드시 따른다.

```json
{
  "check_id": "brain_delta_density_verified",
  "passed": true,
  "actual": {
    "brain_delta_record_count_from_final_block": 165,
    "training_eligible_record_count_from_final_block": 148,
    "counts_by_record_type_from_final_block": {
      "supervised_issuer_day_case": 28,
      "supervised_direct_event_case": 28,
      "theme_formation_case": 7,
      "blind_leader_preference_pair": 20,
      "candidate_generation_error_case": 8,
      "ranking_error_case": 1,
      "newsless_or_unexplained_case": 43,
      "negative_control_case": 13
    }
  },
  "expected": {
    "expected_brain_delta_min": 165,
    "expected_training_eligible_min": 1,
    "expected_counts_by_record_type_min": {
      "supervised_issuer_day_case": 28,
      "supervised_direct_event_case": 28,
      "theme_formation_case": 7,
      "blind_leader_preference_pair": 20,
      "candidate_generation_error_case": 8,
      "ranking_error_case": 1,
      "newsless_or_unexplained_case": 43,
      "negative_control_case": 13
    }
  },
  "expected_source": "CANONICAL_GRAPH_PRESEAL_AND_POSTSEAL_COUNTS_RECOMPUTED_BY_VALIDATOR",
  "actual_source": "FINAL_MARKDOWN_BLOCK_REPARSE",
  "severity": "critical",
  "error_ids": []
}
```

금지 형식:

```json
{"check_id":"brain_delta_density_verified","actual":true,"expected":true,"passed":true}
{"check_id":"brain_delta_density_verified","actual":"ok","expected":"ok","passed":true}
{"check_id":"brain_delta_density_verified","passed":true}
{"check_id":"brain_delta_density_verified","actual":{"value":true},"expected":{"value":true},"passed":true}
```

다음 중 하나라도 있으면 `ACCEPT_FULL` 금지다.

```text
count-gate critical check의 actual이 boolean
count-gate critical check의 expected가 boolean
count-gate critical check의 actual.value가 boolean
count-gate critical check의 expected.value가 boolean
count-gate critical check의 actual이 문자열 ok/pass/verified
count-gate critical check의 expected가 문자열 ok/pass/verified
count-gate critical check에 expected_source가 없음
count-gate critical check에 actual_source가 없음
actual_source != FINAL_MARKDOWN_BLOCK_REPARSE
expected_source가 GENERATED_OUTPUT 또는 SELF_DECLARED_MANIFEST
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATOR_BOOLEAN_PROXY
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
validator_exit_code = 2
```

## V27.2 validator는 brain_delta actual을 final Markdown에서 다시 세야 한다

validator는 internal canonical_graph 변수나 renderer가 넘겨준 메모리를 actual로 쓰지 않는다. 반드시 최종 Markdown 파일을 다시 열어 marker block을 자른 뒤 `brain_delta.jsonl`을 parse한다.

검증 절차:

```text
1. final bundle Markdown 파일을 byte로 다시 읽는다.
2. <!-- NSLAB:BEGIN brain_delta.jsonl --> ... <!-- NSLAB:END brain_delta.jsonl --> block을 정확히 1회 찾는다.
3. code fence가 있으면 제거하고 JSONL non-empty line만 추출한다.
4. 각 line을 json.loads 한다.
5. line count를 actual_brain_delta_record_count로 계산한다.
6. training_eligible == true인 line count를 actual_training_eligible_record_count로 계산한다.
7. record_type별 count를 actual_counts_by_record_type으로 계산한다.
8. record_id 중복, record_type 누락, episode_id/trade_date 누락, provenance_source_ids 누락을 계산한다.
9. 이 actual 값만 validation_report와 direct_ingest_contract에 쓴다.
```

다음은 invalid다.

```text
actual_brain_delta_record_count = canonical_graph["brain_delta_count"]
actual_brain_delta_record_count = research_episode["brain_delta_record_count"]
actual_brain_delta_record_count = direct_ingest_contract.record_import_manifest.brain_delta_record_count
actual_brain_delta_record_count = 이미 렌더링할 때 계산한 local variable
actual_brain_delta_record_count = validation_report 안에 이미 쓰인 값
```

`actual_*`는 반드시 final Markdown block reparse에서만 온다.

## V27.3 expected_brain_delta_min 계산식 고정

정상 거래일이고 `outcome_ledger_count > 0`이면 expected는 다음 두 값 중 더 큰 값이다.

```text
expected_brain_delta_min_full = max(
    100,
    issuer_day_case_count
  + supervised_direct_event_case_count
  + theme_formation_case_count
  + blind_leader_pair_count
)
  + candidate_generation_error_case_count
  + ranking_error_case_count
  + newsless_or_unexplained_case_count
  + negative_control_case_count

expected_brain_delta_min_conservative = max(
    100,
    issuer_day_case_count
  + direct_event_case_count
  + theme_formation_case_count
  + blind_leader_pair_count
)

expected_brain_delta_min = max(
    expected_brain_delta_min_full,
    expected_brain_delta_min_conservative
)
```

각 count의 source는 다음으로 제한한다.

```text
issuer_day_case_count:
  ledger_population_audit.json 또는 canonical_graph postseal issuer-day population에서 validator가 재계산

supervised_direct_event_case_count / direct_event_case_count:
  candidate_screening.jsonl + entity_ledger_blind.jsonl + fact_ledger_blind.jsonl을 validator가 재계산

theme_formation_case_count:
  sealed theme universe + postseal outcome join에서 validator가 재계산

blind_leader_pair_count:
  blind_prediction.json의 sealed pair list에서 validator가 재계산

candidate_generation_error_case_count:
  outcome_to_news_audit.jsonl에서 classification == CANDIDATE_GENERATION_MISS인 record count

ranking_error_case_count:
  outcome_to_news_audit.jsonl에서 classification == RANKING_MISS 또는 SCREENED_OUT_BUT_WINNER인 record count

newsless_or_unexplained_case_count:
  outcome_to_news_audit.jsonl에서 classification == NEWSLESS_OR_UNEXPLAINED인 record count

negative_control_case_count:
  candidate_screening.jsonl 또는 brain_delta에서 negative_control_case로 기록된 count. 단, expected에 넣는 경우 source artifact가 별도로 존재해야 한다.
```

금지:

```text
expected_brain_delta_min = actual_brain_delta_record_count
expected_brain_delta_min = len(brain_delta.jsonl)
expected_brain_delta_min = validation_report 안에 이미 적힌 숫자
expected_brain_delta_min = direct_ingest_contract 안에 이미 적힌 숫자
expected_brain_delta_min = 7
expected_brain_delta_min = 32
expected_brain_delta_min = 45
```

## V27.4 actual < expected이면 수리 또는 quarantine

아래 조건은 절대 warning이 아니다.

```text
actual_brain_delta_record_count < expected_brain_delta_min
```

처리:

```text
1. ACCEPT_FULL 금지
2. direct_brain_ingest_ready=false
3. brain_eligible=false
4. validator_exit_code=2
5. critical_error_count += 1
6. bundle_status = QUARANTINE_BRAIN_DELTA_UNDERFILLED
```

단, 이 오류는 대부분 recoverable이다. 즉시 최종 quarantine으로 끝내지 말고 먼저 아래 수리를 시도한다.

```text
canonical_graph의 issuer_day/direct_event/theme/pair/error population에서 누락된 brain_delta record를 생성한다.
brain_delta.jsonl을 record-level로 보강한다.
record_id를 재배정한다.
id_registry.json에 모든 brain_delta id를 추가한다.
bundle_manifest.json에 brain_delta block sha256/byte_size/line_count를 갱신한다.
validation_report.json을 final Markdown reparse 기반으로 갱신한다.
direct_ingest_contract.json record_import_manifest를 갱신한다.
final bundle을 재렌더링한다.
validator를 재실행한다.
```

수리 후에도 `actual < expected`이면 그때만 `QUARANTINE_BRAIN_DELTA_UNDERFILLED`로 종료한다.

## V27.5 brain_delta type parity는 equality가 아니라 minimum coverage다

모든 날짜가 모든 record_type을 갖지는 않는다. 따라서 특정 type이 0일 수 있다. 하지만 expected에 포함된 type은 실제 count가 expected 이상이어야 한다.

필수 schema:

```json
{
  "check_id": "brain_delta_type_count_parity_verified",
  "passed": true,
  "actual": {
    "counts_by_record_type_from_final_block": {
      "supervised_issuer_day_case": 28,
      "supervised_direct_event_case": 28,
      "theme_formation_case": 7,
      "blind_leader_preference_pair": 20,
      "candidate_generation_error_case": 8,
      "ranking_error_case": 1,
      "newsless_or_unexplained_case": 43,
      "negative_control_case": 13
    }
  },
  "expected": {
    "minimum_counts_by_record_type": {
      "supervised_issuer_day_case": 28,
      "supervised_direct_event_case": 28,
      "theme_formation_case": 7,
      "blind_leader_preference_pair": 20,
      "candidate_generation_error_case": 8,
      "ranking_error_case": 1,
      "newsless_or_unexplained_case": 43,
      "negative_control_case": 13
    }
  },
  "expected_source": "LEDGER_POPULATION_AUDIT_AND_OUTCOME_TO_NEWS_AUDIT_RECOMPUTED",
  "actual_source": "FINAL_MARKDOWN_BRAIN_DELTA_JSONL_REPARSE",
  "severity": "critical",
  "error_ids": []
}
```

다음은 실패다.

```text
expected type count가 28인데 actual이 0
expected type count가 28인데 actual이 12
actual counts_by_record_type가 없음
expected minimum_counts_by_record_type가 없음
record_type이 없는 brain_delta line 존재
record_type이 허용 목록 밖임
```

## V27.6 direct_ingest_contract brain_delta mirror 필수

`direct_ingest_contract.json.record_import_manifest`에는 다음 필드가 반드시 있어야 한다.

```json
{
  "expected_brain_delta_min": 0,
  "actual_brain_delta_record_count": 0,
  "actual_training_eligible_record_count": 0,
  "brain_delta_block_line_count": 0,
  "brain_delta_jsonl_sha256": "",
  "brain_delta_counts_by_record_type_actual": {},
  "brain_delta_counts_by_record_type_expected_min": {},
  "candidate_ranking_audit_count": 0,
  "candidate_screening_include_or_watch_count": 0,
  "candidate_ranking_audit_jsonl_sha256": "",
  "validation_report_sha256": "",
  "bundle_manifest_sha256": "",
  "brain_delta_density_verified": true,
  "brain_delta_type_count_parity_verified": true,
  "brain_delta_actual_source": "FINAL_MARKDOWN_BLOCK_REPARSE",
  "brain_delta_expected_source": "CANONICAL_GRAPH_PRESEAL_AND_POSTSEAL_COUNTS_RECOMPUTED_BY_VALIDATOR"
}
```

`direct_brain_ingest_ready=true`이면 다음도 true여야 한다.

```text
record_import_manifest.actual_brain_delta_record_count >= record_import_manifest.expected_brain_delta_min
record_import_manifest.actual_brain_delta_record_count == brain_delta block non-empty jsonl line count
record_import_manifest.brain_delta_jsonl_sha256 == bundle_manifest.block_hashes["brain_delta.jsonl"].sha256
record_import_manifest.candidate_ranking_audit_count == candidate_ranking_audit block non-empty jsonl line count
record_import_manifest.candidate_ranking_audit_count == candidate_screening_include_or_watch_count
record_import_manifest.candidate_ranking_audit_jsonl_sha256 == bundle_manifest.block_hashes["candidate_ranking_audit.jsonl"].sha256
record_import_manifest.brain_delta_density_verified == validation_report.checks["brain_delta_density_verified"].passed
record_import_manifest.brain_delta_actual_source == FINAL_MARKDOWN_BLOCK_REPARSE
record_import_manifest.validation_report_sha256 == bundle_manifest.block_hashes["validation_report.json"].sha256
```

위반 시:

```text
bundle_status = QUARANTINE_DIRECT_INGEST_CONTRACT_MISMATCH
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V27.7 `brain_eligible`의 정의를 count > 0에서 import-ready로 교체

아래 정의는 더 이상 허용되지 않는다.

```text
training_eligible brain_delta record_count > 0 이면 brain_eligible=true
```

새 정의:

```text
brain_eligible=true iff
  direct_brain_ingest_ready == true
  and actual_brain_delta_record_count >= expected_brain_delta_min
  and actual_training_eligible_record_count > 0
  and all critical validation checks passed
  and fatal_blockers == []
```

`brain_delta_record_count > 0`만으로는 brain_eligible이 아니다.

## V27.8 bundle_status ACCEPT_FULL 최종 결정 순서 고정

`bundle_status: ACCEPT_FULL`은 맨 마지막에만 쓴다. 다음 순서를 지킨다.

```text
1. canonical_graph 생성
2. artifacts 렌더링
3. final Markdown assemble
4. validator가 final Markdown을 다시 parse
5. validation_report 생성
6. direct_ingest_contract 생성
7. bundle_manifest 생성
8. final Markdown 재assemble
9. validator second-pass 재실행
10. second-pass 결과가 모두 통과할 때만 front matter bundle_status=ACCEPT_FULL, brain_eligible=true, direct_brain_ingest_ready=true를 쓴다
```

금지:

```text
front matter에 ACCEPT_FULL을 먼저 쓰고 validator를 맞추기
validation_report의 passed boolean을 먼저 쓰고 count를 나중에 채우기
direct_ingest_contract를 먼저 ready=true로 쓰고 fatal_blockers를 나중에 지우기
```

## V27.9 V27 필수 check_id

`validation_report.json`에는 아래 check_id가 반드시 존재해야 한다. 모두 critical이다.

```text
brain_delta_density_verified
brain_delta_expected_min_numeric_verified
brain_delta_actual_count_from_final_block_verified
brain_delta_expected_source_not_generated_output_verified
brain_delta_actual_source_final_markdown_block_reparse_verified
brain_delta_type_count_parity_verified
brain_delta_training_eligible_density_verified
brain_delta_record_type_allowed_verified
brain_delta_block_line_count_verified
brain_delta_payload_hash_verified
record_import_manifest_brain_delta_parity_verified
direct_ingest_contract_brain_delta_mirror_verified
front_matter_accept_full_written_after_second_pass_verified
```

하나라도 누락되면:

```text
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATOR_MISSING_V27_CHECKS
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V27.10 20241028류 산출물에 대한 판정 fixture

아래 조건을 가진 bundle은 반드시 `ACCEPT_FULL` 금지다.

```text
bundle_status == ACCEPT_FULL
brain_eligible == true
direct_brain_ingest_ready == true
phase/preseal outcome checks pass
required marker blocks pass
but actual_brain_delta_record_count < expected_brain_delta_min
```

또는:

```text
brain_delta_density_verified actual == true expected == true
brain_delta_expected_min_verified actual == true expected == true
brain_delta_type_count_parity_verified actual == true expected == true
```

이 fixture를 validator regression test에 포함한다.

기대 판정:

```text
bundle_status = QUARANTINE_BRAIN_DELTA_UNDERFILLED
brain_eligible = false
direct_brain_ingest_ready = false
validator_exit_code = 2
fatal_blockers contains BRAIN_DELTA_UNDERFILLED or VALIDATOR_BOOLEAN_PROXY
```

## V27.11 좋은 기준 fixture

20241204형 성공 샘플처럼 다음 형태는 허용된다.

```text
brain_delta_density_verified:
  actual = 165
  expected = >=165
  passed = true

brain_delta_expected_source = CANONICAL_GRAPH_PRESEAL_OR_POSTSEAL_COUNTS
expected_brain_delta_min = 165
brain_delta_record_count = 165
brain_delta_count_by_type = { ... integer counts ... }
outcome_to_news_audit_stats = { ... integer counts ... }
```

중요한 것은 숫자 165 자체가 아니라, actual과 expected가 숫자이고 expected_source와 actual_source가 분리되어 있으며 final Markdown block을 다시 parse해 actual을 얻는 구조다.


────────────────────────────────────────
V26 IMPORT-SHAPE REGRESSION LOCK — marker·seal receipt·phase finalization 고정
────────────────────────────────────────

이 섹션은 `V25 ACQUISITION FAST-PATH LOCK`, `V24 OUTCOME AIR-GAP LOCK`, `V23 ZERO-CONTAMINATION LOCK`, `V22 REGRESSION LOCK`, `V21 REGRESSION LOCK`, `DIRECT-INGEST GOLD LOCK`, `GOLD-RUN HARD GUARD`보다 우선한다.

v26의 목적은 예측 적중률을 높이는 것이 아니다. 예측은 틀릴 수 있고, 틀린 예측도 두뇌가 학습해야 할 중요한 반례다. 그러나 `ACCEPT_FULL + direct_brain_ingest_ready=true`를 선언하는 bundle은 repo importer가 사람 후검수 없이 읽을 수 있는 gold-shape 구조를 반드시 가져야 한다.

20241101류 회귀의 형태는 다음과 같다.

```text
phase 오염은 없고 brain_delta 밀도도 충분하지만,
research_report.md 또는 postmortem_report.md가 NSLAB marker block으로 존재하지 않거나,
blind_seal_receipt.json 독립 block이 없거나,
phase_state가 PHASE_2_BLIND_PACKET_SEALED에 머문 채 ACCEPT_FULL을 선언하거나,
front matter / validation_report / direct_ingest_contract가 필수 구조 검증을 누락한다.
```

이런 산출물은 분석 내용이 좋아도 자동 import-ready `ACCEPT_FULL`이 아니다. 반드시 수리 후 `ACCEPT_FULL`로 만들거나, 수리 실패 시 `QUARANTINE_IMPORT_SHAPE_CONTRACT`로 둔다.

## V26.0 예측 성과는 구조 hard gate가 아니다

다음은 `ACCEPT_FULL` 실패 사유가 아니다.

```text
final_watchlist가 상한가를 못 맞힘
final high_return >= 10% 개수가 적음
직접뉴스 후보가 시장기억·우선주·수급성 winner를 놓침
상한가 winner가 NEWSLESS_OR_UNEXPLAINED로 많이 남음
ranking_error_case가 많음
candidate_generation_error_case가 많음
```

이들은 `postmortem_report.md`, `outcome_to_news_audit.jsonl`, `brain_delta.jsonl`에 남길 학습 재료다.

반대로 다음은 예측 성과와 무관하게 `ACCEPT_FULL` 금지다.

```text
필수 NSLAB marker block 누락
필수 block이 marker 밖의 일반 본문으로만 존재
phase_state가 최종 완료 phase가 아님
blind_seal_receipt.json block 누락
access_log상 blind seal receipt보다 outcome access가 먼저임
renderer/validator 독립성 증명 누락
validation_report가 actual/expected/expected_source object check가 아님
bundle_manifest가 block별 sha256/byte_size를 재계산하지 않음
direct_ingest_contract가 필수 구조 gate를 반영하지 않음
```

## V26.1 필수 NSLAB marker block exact contract

`ACCEPT_FULL` bundle에는 아래 block이 **정확히 1회씩** 존재해야 한다.

```text
research_report.md
blind_report.md
postmortem_report.md
phase_state.json
access_log.jsonl
acquisition_warnings.jsonl
attempt_history.jsonl
repair_log.jsonl
blind_seal_receipt.json
blind_packet_manifest.json
blind_prediction.json
row_disposition.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
final_semantic_audit.jsonl
market_state_override_audit.jsonl
body_table_candidate_generation_audit.jsonl
ledger_population_audit.json
outcome_ledger.jsonl
outcome_leader_census.jsonl
outcome_to_news_audit.jsonl
entity_resolution.jsonl
research_episode.json
brain_delta.jsonl
source_ledger.jsonl
id_registry.json
validation_report.json
phase_audit_report.json
bundle_manifest.json
direct_ingest_contract.json
```

각 block은 다음 literal marker를 사용한다.

```text
<!-- NSLAB:BEGIN <artifact_name> -->
...
<!-- NSLAB:END <artifact_name> -->
```

규칙:

```text
begin marker count == required_block_count
end marker count == required_block_count
each required artifact has exactly one BEGIN and exactly one END
BEGIN/END nesting is invalid
artifact name typo is invalid
중복 marker는 invalid
marker 밖의 일반 Markdown heading은 required block을 대체하지 못한다
.json block은 json.loads 성공
.jsonl block은 모든 non-empty line이 json.loads 성공
.md block은 비어 있지 않음
```

예를 들어 문서 상단에 `# NSLAB Research Episode Bundle`, `# 연구 episode 개요`, `## Execution receipt`가 있어도 아래 marker가 없으면 `research_report.md`는 누락이다.

```text
<!-- NSLAB:BEGIN research_report.md -->
...
<!-- NSLAB:END research_report.md -->
```

마찬가지로 postmortem 요약이 일반 본문에 있어도 아래 marker가 없으면 `postmortem_report.md`는 누락이다.

```text
<!-- NSLAB:BEGIN postmortem_report.md -->
...
<!-- NSLAB:END postmortem_report.md -->
```

누락 또는 중복 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_REQUIRED_MARKER_BLOCK_MISSING
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
validator_exit_code = 2
```

## V26.2 report block role contract

세 report block은 역할이 다르다.

```text
research_report.md:
  - 전체 episode를 사람이 읽는 통합 보고서
  - BLIND 요약과 POSTMORTEM 요약을 모두 포함 가능
  - 단, BLIND와 POSTMORTEM 경계를 명시해야 함

blind_report.md:
  - PHASE_2 이전에 봉인된 BLIND 전용 보고서
  - outcome 숫자, high_return, upper_limit, outcome response, 적중/실패 표현 금지
  - post-seal 결과를 포함하면 BLIND 오염 또는 report role violation

postmortem_report.md:
  - PHASE_3 이후 outcome 공개 뒤 생성된 사후 보고서
  - outcome snapshot hash/row_count/winner census/final 결과를 포함해야 함
  - 해당 block이 없으면 postmortem이 일반 본문에 있어도 ACCEPT_FULL 금지
```

validator는 다음을 계산한다.

```text
research_report_marker_present == true
blind_report_marker_present == true
postmortem_report_marker_present == true
blind_report_outcome_terms_count == 0
postmortem_report_outcome_summary_present == true
report_block_role_contract_verified == true
```

## V26.3 blind_seal_receipt.json 독립 block 필수

`blind_packet_manifest.json` 안에 seal 관련 필드가 있어도 충분하지 않다. `ACCEPT_FULL`에는 독립 block으로 `blind_seal_receipt.json`이 반드시 있어야 한다.

최소 스키마:

```json
{
  "schema_version": "nslab.blind_seal_receipt.v1",
  "episode_id": "",
  "trade_date": "",
  "created_at": "",
  "phase": "PHASE_2_BLIND_PACKET_SEALED",
  "blind_namespace_readonly_after": true,
  "blind_packet_manifest_sha256": "",
  "sealed_blind_report_sha256": "",
  "sealed_artifacts": [
    {"name": "blind_prediction.json", "sha256": "", "byte_size": 0},
    {"name": "blind_report.md", "sha256": "", "byte_size": 0},
    {"name": "row_disposition.jsonl", "sha256": "", "byte_size": 0},
    {"name": "entity_ledger_blind.jsonl", "sha256": "", "byte_size": 0},
    {"name": "fact_ledger_blind.jsonl", "sha256": "", "byte_size": 0},
    {"name": "inference_ledger_blind.jsonl", "sha256": "", "byte_size": 0},
    {"name": "candidate_screening.jsonl", "sha256": "", "byte_size": 0},
    {"name": "final_semantic_audit.jsonl", "sha256": "", "byte_size": 0},
    {"name": "blind_packet_manifest.json", "sha256": "", "byte_size": 0}
  ],
  "preseal_outcome_download_count": 0,
  "preseal_outcome_stat_count": 0,
  "preseal_outcome_sha256_count": 0,
  "preseal_outcome_header_read_count": 0,
  "preseal_outcome_row_count_count": 0,
  "preseal_outcome_parse_count": 0,
  "preseal_outcome_label_calculation_count": 0,
  "preseal_outcome_winner_census_count": 0,
  "outcome_unlock_allowed_after_receipt": true,
  "receipt_written_before_any_outcome_access": true
}
```

`access_log.jsonl`에는 다음 순서가 드러나야 한다.

```text
WRITE blind_prediction.json / blind_report.md / row_disposition.jsonl / ...
HASH blind artifacts
WRITE blind_packet_manifest.json
WRITE blind_seal_receipt.json
VERIFY blind_seal_receipt.json
ONLY THEN DOWNLOAD/OPEN/PARSE outcome_snapshot
```

validator hard check:

```text
blind_seal_receipt_block_present == true
blind_seal_receipt_schema_verified == true
blind_seal_receipt_sha_matches_front_matter == true
blind_seal_receipt_preseal_counters_zero == true
access_log_receipt_before_outcome_access == true
```

## V26.4 phase_state finalization contract

`ACCEPT_FULL`에서 `phase_state.json`은 PHASE_2에 멈춰 있으면 안 된다.

허용 final state:

```text
current_phase == PHASE_5_FINAL_ACCEPT_OR_QUARANTINE
또는
phase == PHASE_4_BUNDLE_COMPLETE
또는
phase_state == FINAL_BUNDLE_ACCEPTED
```

금지 final state:

```text
PHASE_0_ACQUIRE_INPUT
PHASE_1_BUILD_BLIND_ONLY
PHASE_2_BLIND_PACKET_SEALED
PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL
```

`phase_state.json` 최소 필드:

```json
{
  "schema_version": "nslab.phase_state.v26",
  "current_phase": "PHASE_5_FINAL_ACCEPT_OR_QUARANTINE",
  "phase_history": [
    "PHASE_0_ACQUIRE_INPUT",
    "PHASE_1_BUILD_BLIND_ONLY",
    "PHASE_2_BLIND_PACKET_SEALED",
    "PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL",
    "PHASE_4_RENDER_VALIDATE_REPAIR",
    "PHASE_5_FINAL_ACCEPT_OR_QUARANTINE"
  ],
  "accepted_attempt_valid": true,
  "blind_sealed": true,
  "blind_seal_receipt_verified": true,
  "outcome_access_allowed": true,
  "outcome_opened_after_blind_seal_receipt": true,
  "postseal_outcome_download_count": 1,
  "postseal_outcome_parse_count": 1,
  "bundle_rendered": true,
  "validator_completed": true,
  "validator_exit_code": 0,
  "critical_error_count": 0,
  "fatal_blockers": [],
  "bundle_status": "ACCEPT_FULL",
  "brain_eligible": true,
  "direct_brain_ingest_ready": true
}
```

validator hard check:

```text
phase_state_block_present == true
phase_state_finalized_verified == true
phase_history_complete_verified == true
phase_state_not_left_at_phase_2 == true
phase_state_matches_access_log_order == true
```

## V26.5 renderer/validator/front-matter proof contract

`ACCEPT_FULL` front matter에는 다음 필드가 반드시 있어야 한다.

```text
renderer_version
renderer_sha256
validator_version
validator_sha256
validator_exit_code
canonical_graph_sha256
canonical_graph_object_counts
bundle_manifest_sha256
validation_report_sha256
phase_audit_report_sha256
blind_seal_receipt_sha256
direct_ingest_contract_sha256
```

`renderer_sha256 == validator_sha256`이면 기본적으로 fatal이다. combined script 예외를 쓰려면 다음 필드가 모두 있어야 하며 서로 일관되어야 한다.

```text
combined_tool_sha256
renderer_entrypoint_sha256
validator_entrypoint_sha256
renderer_entrypoint_sha256 != validator_entrypoint_sha256
validator_does_not_import_renderer_state == true
validator_recomputes_from_final_bundle_and_artifact_blocks == true
validator_expected_source_not_generated_output == true
```

combined 예외 필드가 누락되면:

```text
bundle_status = QUARANTINE_VALIDATOR_NOT_INDEPENDENT
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V26.6 validation_report object check 필수

`validation_report.json`은 문자열 리스트가 아니라 object 배열이어야 한다.

각 critical check는 다음 필드를 가진다.

```json
{
  "check_id": "required_marker_blocks_exactly_once_verified",
  "passed": true,
  "actual": {},
  "expected": {},
  "expected_source": "PROMPT_CONSTANT | INPUT_PARSE | ACCESS_JSON | CANONICAL_GRAPH_PRESEAL | POSTSEAL_OUTCOME_PARSE | FINAL_BUNDLE_REPARSE",
  "actual_source": "FINAL_BUNDLE_REPARSE | FINAL_MARKDOWN_BLOCK_REPARSE | ACCESS_LOG_REPARSE | VALIDATOR_RECOMPUTE",
  "severity": "critical",
  "error_ids": []
}
```

필수 check_id:

```text
required_marker_blocks_exactly_once_verified
research_report_marker_present_verified
postmortem_report_marker_present_verified
report_block_role_contract_verified
blind_seal_receipt_block_verified
blind_seal_receipt_before_outcome_access_verified
phase_state_finalized_verified
phase_history_complete_verified
phase_state_not_left_at_phase_2_verified
access_log_phase_order_verified
front_matter_gold_shape_verified
renderer_validator_independence_verified
bundle_manifest_block_hashes_verified
direct_ingest_contract_mirrors_validation_verified
prediction_performance_not_used_as_structural_gate_verified
candidate_ranking_audit_schema_verified
candidate_ranking_audit_rankable_coverage_verified
candidate_ranking_audit_final_count_verified
candidate_ranking_audit_nonfinal_reason_verified
candidate_ranking_audit_alias_zero_verified
brain_delta_record_type_canonical_verified
brain_delta_noncanonical_alias_zero_verified
direct_ingest_contract_count_hash_mirror_verified
```

금지:

```text
checks: ["ok", "passed", "all good"]
check_id만 있고 actual/expected/expected_source가 없음
expected_source == GENERATED_OUTPUT
expected_source == SELF_DECLARED_MANIFEST
```

## V26.7 bundle_manifest block hash 재계산

`bundle_manifest.json`은 final Markdown에서 marker block 내용을 다시 잘라내어 각 block의 `sha256`과 `byte_size`를 계산해야 한다.

최소 필드:

```json
{
  "schema_version": "nslab.bundle_manifest.v26",
  "episode_id": "",
  "required_block_count": 32,
  "required_blocks_present_count": 32,
  "missing_required_blocks": [],
  "duplicate_blocks": [],
  "block_hashes": {
    "research_report.md": {"sha256": "", "byte_size": 0},
    "postmortem_report.md": {"sha256": "", "byte_size": 0},
    "blind_seal_receipt.json": {"sha256": "", "byte_size": 0},
    "phase_state.json": {"sha256": "", "byte_size": 0}
  },
  "front_matter_hash_fields_match_blocks": true,
  "bundle_sha256": ""
}
```

## V26.8 direct_ingest_contract mirror gate

`direct_ingest_contract.json`은 `validation_report.json`의 critical 결과를 mirror해야 한다.

최종 bundle에서 `direct_ingest_contract.json.status`가 `READY_FOR_FINAL_REPARSE` 또는 `PENDING_FINAL_REPARSE`이면 아직 중간 산출물이다. 이 상태로 `ACCEPT_FULL`, `brain_eligible=true`, `direct_brain_ingest_ready=true`를 선언하면 안 된다.

필수 hard_gate_summary:

```json
{
  "required_marker_blocks_exactly_once_verified": true,
  "research_report_marker_present_verified": true,
  "postmortem_report_marker_present_verified": true,
  "blind_seal_receipt_block_verified": true,
  "phase_state_finalized_verified": true,
  "phase_state_not_left_at_phase_2_verified": true,
  "access_log_phase_order_verified": true,
  "renderer_validator_independence_verified": true,
  "bundle_manifest_block_hashes_verified": true,
  "critical_error_count": 0,
  "validator_exit_code": 0
}
```

`direct_brain_ingest_ready=true`인데 위 값 중 하나라도 false이거나, 아래 top-level 필드 중 하나라도 없으면 오류다.

필수 top-level 필드:

```text
episode_id
trade_date
direct_brain_ingest_ready
brain_eligible
automated_import_expected_to_pass
requires_manual_research_review
requires_posthoc_prompt_repair
requires_human_semantic_review
fatal_blockers
hard_gate_summary
record_import_manifest
```

금지 top-level 상태:

```text
status == READY_FOR_FINAL_REPARSE
status == PENDING_FINAL_REPARSE
direct_brain_ingest_ready missing
automated_import_expected_to_pass missing
requires_human_semantic_review missing
fatal_blockers missing
```

```text
bundle_status = QUARANTINE_DIRECT_INGEST_CONTRACT_MISMATCH
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V26.9 수리 우선, 실패 시 quarantine

V26 위반은 대부분 recoverable render/validate 오류다. 즉시 연구를 버리지 말고 canonical_graph 또는 renderer를 고친 뒤 재렌더·재검증한다.

수리 가능 예:

```text
research_report.md marker 누락
postmortem_report.md marker 누락
blind_seal_receipt.json block 누락
phase_state가 PHASE_2에 머문 채 최종 렌더됨
bundle_manifest가 marker hash를 빠뜨림
validation_report check가 문자열 리스트로 렌더됨
```

수리 후에도 위반이 남으면:

```text
bundle_status = QUARANTINE_IMPORT_SHAPE_CONTRACT
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```



────────────────────────────────────────
V25 ACQUISITION FAST-PATH LOCK — curl 루프 금지와 web/download 우선 확보
────────────────────────────────────────

이 섹션은 `V24 OUTCOME AIR-GAP LOCK`보다 먼저 적용되는 입력 확보 전용 규칙이다. 단, 이 섹션은 파일 확보 방식만 정하며, PHASE_2 전 outcome snapshot byte 접근 금지와 outcome path materialization 금지는 절대 완화하지 않는다.

핵심 원칙:

```text
curl/urllib/requests는 선택적 진단 도구일 뿐, GitHub 파일 확보의 필수 경로가 아니다.
ChatGPT 세션에서 container DNS가 실패하면 즉시 web/browser + download tool 경로로 전환한다.
같은 URL에 대해 curl/urllib/requests 실패를 반복하지 않는다.
```

## V25.0 첫 행동

MAIN PROMPT, 선택 news CSV, research_daily manifest/schema/calendar/access JSON, blind snapshot을 확보할 때 첫 판단은 다음 순서로 한다.

```text
1. web/browser 계층으로 GitHub blob 또는 raw page를 연다.
2. 파일명, 경로, Raw/View raw 존재, 날짜 일치를 확인한다.
3. download tool 또는 파일 분석 도구로 로컬 저장을 시도한다.
4. 로컬 저장 후 sha256, byte_size, header, full parse를 검증한다.
5. shell/curl/urllib/requests는 0~1회 진단으로만 사용한다.
6. shell/curl/urllib/requests DNS 실패가 나오면 즉시 acquisition_warnings에 기록하고 web/download fallback으로 진행한다.
```

다음 오류는 파일 부재가 아니다.

```text
curl: Could not resolve host
urllib.request.URLError Temporary failure in name resolution
socket.gaierror
NETWORK=caas_packages_only
```

위 오류만으로 다음 상태를 선언하지 않는다.

```text
MAIN_PROMPT_ACQUIRE_FAILED
CSV_ACQUIRE_FAILED
ACCESS_JSON_ACQUIRE_FAILED
PRICE_SOURCE_MISSING
INPUT_UNPARSED
```

## V25.1 curl 반복 금지

같은 파일에 대해 아래처럼 반복하지 않는다.

```text
curl raw.githubusercontent 실패
→ urllib raw.githubusercontent 실패
→ requests raw.githubusercontent 실패
→ curl github.com/raw 실패
→ 다시 raw.githubusercontent 검색
```

이 패턴은 시간만 쓰고 파일 부재를 증명하지 못한다. 한 번 실패하면 다음으로 전환한다.

```text
web_open_blob_or_raw
→ download_tool_save
→ if JSON content-type blocked: web_open text capture 후 canonical local JSON 저장
→ if CSV download blocked: GitHub raw endpoint, raw.githubusercontent endpoint, Contents API raw media type, codeload ZIP 순서로 fallback
```

## V25.2 MAIN PROMPT 확보 규칙

`docs/research_prompt.md`는 긴 파일이므로 web.open line preview를 사람이 여러 번 스크롤해 전체 프롬프트로 삼지 않는다.

성공 조건은 다음뿐이다.

```text
/mnt/data/.../research_prompt.md 존재
byte_size > 0
sha256 계산 완료
utf-8 decode 성공
첫 줄/상단 revision_goal 확인
```

web/browser preview는 존재 확인용이다. 전체 실행 프롬프트는 반드시 로컬 저장 파일을 기준으로 읽는다.

## V25.3 CSV 확보 규칙

선택된 `news_YYYYMMDD.csv`는 HTML preview를 연구 입력으로 쓰지 않는다.

성공 조건:

```text
local basename == selected_input_file
byte_size > 0
sha256 계산 완료
columns == page,row,date,time,title,body
CSV full parse 성공
row_count > 0
min/max published_at 계산 성공
time_unverified_rows 계산 완료
tab/LF/CR 제외 control char count == 0
```

GitHub 화면이 `too large to display`를 보여도 파일 부재가 아니다. Raw/download fallback을 계속한다.

## V25.4 JSON 확보 규칙

`manifest.json`, `schema.json`, `access/YYYY/MM/YYYYMMDD.json`은 download tool이 `application/json` 또는 content-type 문제로 거부할 수 있다.

이 경우 실패로 보지 않고 다음을 수행한다.

```text
1. web/browser로 JSON raw text를 연다.
2. 표시된 JSON object를 로컬 파일로 저장한다.
3. 저장한 local JSON을 json.loads로 parse한다.
4. parse 성공 후 canonical JSON string을 재직렬화해 sha256을 계산한다.
5. acquisition_warnings에 JSON_DOWNLOAD_TOOL_CONTENT_TYPE_BLOCK을 기록한다.
```

단, stock-web `research_daily` manifest/schema/access JSON은 V25.5의 가격 계층 fast path가 우선한다. 이 JSON들은 routing metadata 확인용이며, bytes를 정확히 저장하지 못한 상태에서 사람이 복사해 canonical JSON으로 재직렬화하거나 sha256을 만든 척하지 않는다.

stock-web JSON bytes 저장이 실패했지만 web/browser로 path metadata를 확인한 경우:

```text
access_sha256_status = WEB_VIEW_ONLY_UNHASHED
research_daily_json_reconstructed_count == 0
acquisition_warnings에 STOCK_WEB_JSON_WEB_VIEW_ONLY_UNHASHED 기록
snapshot CSV Raw bytes 확보로 즉시 이동
```

단, access JSON에서 outcome 관련 값은 다음 locked metadata로만 보존한다.

```text
outcome_snapshot_date
outcome_snapshot_sha256_expected
outcome_snapshot_row_count_expected
outcome_snapshot_bytes_expected
outcome_snapshot_path_locked = LOCKED_UNTIL_PHASE_3
```

PHASE_2 전에는 access JSON의 outcome path를 raw URL, blob URL, local filename, download_manifest, files_to_fetch, snapshot_paths, price_paths로 materialize하지 않는다.

## V25.5 stock-web 가격 계층 확보 fast path

research_daily 가격 계층은 다음 순서로 확보한다.

```text
1. trading_calendar.csv
2. manifest.json
3. schema.json
4. access/YYYY/MM/YYYYMMDD.json
5. access.blind_snapshot_path만 다운로드·parse
6. BLIND packet seal receipt 검증
7. 그 뒤 access.outcome_snapshot_path unlock·download·parse
```

stock-web manifest/schema/access JSON은 routing metadata 확인용이다. 실제 가격 provenance는 blind/outcome snapshot CSV Raw bytes의 sha256, byte_size, header, row_count로 닫는다.

필수 fast path:

```text
stock-web JSON은 shell/curl/urllib/requests로 반복 다운로드하지 않음
web/browser로 Raw JSON을 열어 blind_snapshot_path, outcome_snapshot_path, row_count, sha256 metadata만 확인
JSON bytes를 정확히 저장하지 못하면 access_sha256_status = WEB_VIEW_ONLY_UNHASHED
JSON 내용을 사람이 복사해 새 JSON 파일로 재구성하거나 sha256을 조작하지 않음
JSON 다운로드 실패를 이유로 snapshot CSV 확보를 지연하지 않음
BLIND 전에는 blind_snapshot_path CSV만 Raw bytes로 다운로드·검증
outcome_snapshot_path CSV는 blind_seal_receipt 생성·검증 후에만 Raw bytes로 다운로드·검증
```

PHASE_0/1/2의 download manifest에는 `blind_snapshot`만 들어갈 수 있고 `outcome_snapshot` logical_role은 등장하면 안 된다.

## V25.6 acquisition_warnings는 실패가 아니다

다음은 warning일 뿐이다.

```text
CONTAINER_CURL_DNS_FAILURE
JSON_DOWNLOAD_TOOL_CONTENT_TYPE_BLOCK
GITHUB_LARGE_FILE_PREVIEW_UNAVAILABLE
OUTCOME_BLOB_EXISTENCE_PAGE_OPENED_WITHOUT_RAW_CONTENT
```

warning이 있어도 로컬 저장·검증·full parse가 성공하면 ACCEPT_FULL 가능성을 유지한다.

## V25.7 fallback을 다 쓴 뒤에만 ACQUIRE_FAILED

다음 모든 경로가 실패한 경우에만 ACQUIRE_FAILED를 선언한다.

```text
web/browser blob 확인 실패
web/browser raw 확인 실패
download tool 저장 실패
파일 분석 도구 저장 실패
GitHub raw endpoint 실패
raw.githubusercontent endpoint 실패
Contents API raw media type 실패
codeload ZIP fallback 실패
```

그 전에는 다른 날짜 CSV, 샌드박스 잔존 파일, 최신 CSV, 예시 파일로 대체하지 않는다.

────────────────────────────────────────
V24 OUTCOME AIR-GAP LOCK — outcome 경로·URL·다운로드 계획 자체를 PHASE_2 전에는 만들지 않는다
────────────────────────────────────────

이 섹션은 `V23 ZERO-CONTAMINATION LOCK`, `AUTONOMOUS FULL-RUN LOCK`, `PHASE COCKPIT LOCK`보다 우선한다. v23은 pre-seal outcome byte 접촉을 오염으로 잘 차단했지만, 실행자가 access JSON을 읽은 뒤 outcome snapshot URL이나 local filename을 미리 만들면서 `PRESEAL_OUTCOME_BYTE_ACCESS`가 반복될 수 있다. v24의 목적은 오염을 격리하는 것이 아니라, **PHASE_2 전에는 outcome을 다운로드할 수 있는 경로·변수·루프·manifest가 아예 생기지 않게 하는 것**이다.

핵심 원칙:

```text
PHASE_0/1/2에서는 outcome snapshot을 “안 읽는다”가 아니라,
outcome snapshot raw URL, local filename, download plan, file object를 만들지 않는다.
```

## V24.0 accepted clean run 우선순위

```text
1. PHASE_2 전 outcome URL/path/local filename/download manifest 생성 0건 유지
2. PHASE_2 전 모든 file fetch는 preseal allowlist wrapper로만 수행
3. BLIND packet 물리적 저장·해시·seal receipt 생성
4. seal receipt 검증 후에만 outcome path unlock
5. PHASE_3에서 outcome URL을 처음 조립하고 다운로드·parse
```

## V24.1 access JSON의 outcome path는 preseal에서 잠긴 metadata다

access JSON은 PHASE_0에서 열 수 있다. 그러나 PHASE_0/1/2의 runnable context와 generated artifact에는 outcome 경로를 실행 가능한 형태로 남기지 않는다.

preseal에서 허용되는 outcome metadata:

```json
{
  "outcome_snapshot_date": "YYYY-MM-DD",
  "outcome_snapshot_sha256_expected": "...",
  "outcome_snapshot_row_count_expected": 0,
  "outcome_snapshot_bytes_expected": 0,
  "outcome_snapshot_path_locked": "LOCKED_UNTIL_PHASE_3",
  "outcome_raw_url_materialized_preseal": false,
  "outcome_local_path_materialized_preseal": false
}
```

금지:

```text
outcome_snapshot_path를 raw base URL과 결합
outcome_snapshot_path를 GitHub blob URL과 결합
outcome_snapshot_path에서 basename을 추출해 local filename 생성
outcome_snapshot_path를 download_manifest / files_to_fetch / snapshot_paths / all_paths / price_paths에 추가
outcome_snapshot_path를 curl / wget / urllib / requests / container.download / download tool에 전달
outcome_snapshot_path를 open / stat / os.path.getsize / sha256 / head / tail / grep / wc / file 에 전달
```

주의: outcome path 문자열 자체는 가격 label이 아니므로 이론상 오염은 아니다. 그러나 자동 실행에서는 그 문자열이 곧 다운로드 실수로 이어진다. 그래서 v24 accepted run에서는 PHASE_2 전 runnable code에 실제 outcome path 문자열을 남기지 않는다.

## V24.2 preseal download allowlist wrapper 필수

PHASE_0/1/2에서 다운로드·저장 가능한 logical_role은 다음뿐이다.

```text
main_prompt
news_csv
trading_calendar
research_daily_manifest
research_daily_schema
research_daily_access_json
gold_reference_structure_only
blind_snapshot
```

PHASE_0/1/2에서는 아래 logical_role이 등장하면 즉시 accepted attempt invalid다.

```text
outcome_snapshot
outcome_price_snapshot
D_snapshot
postseal_outcome
result_snapshot
```

preseal download wrapper는 실행 전 다음을 검사한다.

```text
logical_role in PRESEAL_ALLOWED_LOGICAL_ROLES
is_outcome_snapshot == false
url does not contain /snapshots/YYYY/MM/<D_YYYYMMDD>.csv
local_path does not contain outcome_snapshot_<D_YYYYMMDD>.csv
local_path does not contain <D_YYYYMMDD>_outcome.csv
```

validator hard check:

```text
preseal_download_manifest_outcome_role_count == 0
preseal_download_manifest_d_snapshot_url_count == 0
preseal_download_manifest_outcome_local_path_count == 0
preseal_url_materialized_for_outcome_count == 0
preseal_local_filename_materialized_for_outcome_count == 0
```

## V24.3 blind/outcome pair loop 금지

PHASE_2 전에는 blind와 outcome을 같은 자료구조에 넣지 않는다.

금지 예:

```python
for kind in ["blind", "outcome"]: ...
for path in [blind_snapshot_path, outcome_snapshot_path]: ...
snapshot_paths = {"blind": blind_snapshot_path, "outcome": outcome_snapshot_path}
for role, path in snapshot_paths.items(): ...
for date in [previous_trade_date, trade_date]: download_snapshot(date)
```

preseal에서는 blind snapshot만 받는 단일 함수만 허용한다.

```python
def fetch_blind_snapshot_only(access_meta):
    # only access_meta.blind_snapshot_path is used
    # outcome path is locked metadata and cannot be passed here
    ...
```

postseal에서만 outcome URL을 처음 조립한다.

```python
def fetch_outcome_snapshot_postseal(seal_receipt, access_meta):
    assert seal_receipt.exists
    assert seal_receipt.verified_by_validator_precheck is True
    outcome_url = raw_base + access_meta.unlock_outcome_snapshot_path_after_seal()
    ...
```

## V24.4 phase code preflight

PHASE_2 전 실행 코드에 다음 패턴이 있으면 실행하지 말고 코드를 고친다.

```text
raw_base + outcome_snapshot_path
repository_raw_base_url + outcome_snapshot_path
Path(... outcome ...)
open(... outcome ...)
os.path.getsize(... outcome ...)
Path.stat(... outcome ...)
sha256_file(... outcome ...)
pd.read_csv(... outcome ...)
csv.DictReader(... outcome ...)
download(... outcome ...)
curl ... outcome ...
wget ... outcome ...
requests.get(... outcome ...)
urllib.request.urlretrieve(... outcome ...)
for kind in ['blind', 'outcome']
for path in [blind_snapshot_path, outcome_snapshot_path]
```

D 거래일 snapshot 물리 파일명도 PHASE_2 전에는 금지한다.

```text
atlas/research_daily/snapshots/YYYY/MM/<D_YYYYMMDD>.csv
<D_YYYYMMDD>_outcome.csv
outcome_snapshot_<D_YYYYMMDD>.csv
research_daily_outcome_<D_YYYYMMDD>.csv
```

허용 예외:

```text
news_<D_YYYYMMDD>.csv
atlas/research_daily/access/YYYY/MM/<D_YYYYMMDD>.json
```

## V24.5 outcome blob existence page 처리

가능하면 PHASE_2 전에는 GitHub outcome blob page도 열지 않는다. access JSON의 expected metadata가 있으면 outcome blob existence check는 필요 없다.

다만 web/browser 계층이 링크 탐색 중 outcome blob page를 열었고 Raw CSV rows/header/sample/byte/hash/row_count/label을 보지 않았다면 이는 content contamination이 아니다. 이 경우 다음 warning만 남기고 accepted 가능성을 유지한다.

```text
OUTCOME_BLOB_EXISTENCE_PAGE_OPENED_WITHOUT_RAW_CONTENT
```

단, 이것은 자동 탐색 부작용을 감사하기 위한 예외이지 실행 계획이 아니다. deliberate preseal outcome blob existence check는 금지한다.

## V24.6 preseal byte touch 발생 시 같은 실행 ACCEPT_FULL 금지

PHASE_2 전에 outcome 파일이 실제로 다운로드되거나, 파일 존재를 넘어서 stat·byte_size·sha256·header·row_count·parse·sample·label 계산 중 하나라도 실행되면 그 ChatGPT 실행은 더 이상 import-ready 연구가 아니다.

중요한 구분:

```text
preflight wrapper가 outcome download 계획을 실행 전에 차단함
→ 오염 아님. 계속 진행.

outcome blob existence page가 우연히 열렸지만 raw rows/header/byte/hash/row_count/label을 보지 않음
→ content 오염 아님. warning만 기록하고 계속 진행 가능.

outcome 파일이 로컬에 생성됨 또는 byte_size/stat를 계산함
→ same-run ACCEPT_FULL 금지. 최소 QUARANTINE audit만 허용.
```

처리:

```text
attempt_status = INVALID_PRESEAL_OUTCOME_BYTE_TOUCH
accepted_attempt_valid = false
bundle_status = QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
blind_valid = false
forecast_evaluation_eligible = false
brain_eligible = false
direct_brain_ingest_ready = false
final_watchlist_size = 0
outcome_ledger_count = 0
brain_delta_count = 0
fatal_blockers = [PRESEAL_OUTCOME_BYTE_ACCESS]
ACCEPT_FULL 금지
```

금지:

```text
같은 ChatGPT 실행에서 clean restart라고 주장하며 ACCEPT_FULL 생성
오염된 뒤 긴 postmortem 작성
오염된 뒤 winner census 작성
오염된 뒤 brain_delta 생성
오염된 뒤 정치테마 thesis를 사후 재구성
```

허용:

```text
최소 audit bundle 생성
phase_state.json 작성
access_log.jsonl 작성
acquisition_warnings.jsonl 작성
validation_report.json 작성
다음 실행은 깨끗한 새 세션/새 workspace에서 다시 시작
```

## V24.7 post-seal outcome open receipt

PHASE_3에서 outcome을 열기 전에는 다음 receipt가 실제 파일로 있어야 한다.

```json
{
  "receipt_type": "BLIND_SEAL_RECEIPT",
  "blind_prediction_sha256": "...",
  "blind_report_sha256": "...",
  "blind_packet_manifest_sha256": "...",
  "created_before_outcome_open": true,
  "verified_by_validator_precheck": true
}
```

access_log 순서는 반드시 다음과 같아야 한다.

```text
PHASE_2_BLIND_PACKET_SEALED / WRITE_BLIND_SEAL_RECEIPT
PHASE_2_BLIND_PACKET_SEALED / VERIFY_BLIND_SEAL_RECEIPT
PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL / MATERIALIZE_OUTCOME_URL_POSTSEAL
PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL / DOWNLOAD_OUTCOME_SNAPSHOT_POSTSEAL
PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL / PARSE_OUTCOME_SNAPSHOT_POSTSEAL
```

## V24.8 최종 QUARANTINE 허용 조건

최종 QUARANTINE은 다음 경우에만 허용한다.

```text
preseal outcome byte touch가 실제 실행됨
semantic outcome content/label contamination 발생
CSV 또는 MAIN PROMPT를 모든 fallback 후에도 확보 불가
validator critical error를 16회 repair loop 후에도 해소 불가
```

preflight에서 금지 download plan을 발견하고 실행 전에 차단한 것은 QUARANTINE 사유가 아니다. 코드를 고치고 계속 진행한다. 그러나 outcome 파일이 실제로 로컬에 생겼거나 stat/byte_size/sha/header/row_count/parse 중 하나라도 실행되면 그 실행은 최소 audit QUARANTINE으로 닫는다.

## V24.9 v23과의 충돌 해결

v24는 v23의 zero-contamination 조건을 유지하고, 한 단계 앞에서 outcome URL/path/download plan 생성을 차단한다.

```text
v23: preseal outcome byte 접촉 발생 시 최소 audit bundle만 생성한다.
v24: preseal outcome byte 접촉이 애초에 발생하지 않도록 URL/path/download plan/wrapper 단계에서 차단한다.
```

차단은 성공 경로이고, 실행된 byte touch는 실패 경로다.

```text
wrapper가 실행 전에 outcome download를 막음 → 계속 진행 가능
outcome byte touch가 실제 실행됨 → 같은 실행 ACCEPT_FULL 금지, 최소 audit QUARANTINE
```

ACCEPT_FULL accepted attempt에서는 preseal outcome 관련 counter가 모두 0이어야 한다.

────────────────────────────────────────
────────────────────────────────────────
V23 ZERO-CONTAMINATION LOCK — BLIND 오염 방지 최우선 규칙
────────────────────────────────────────

이 섹션은 이번 프롬프트의 최상위 실행 규칙이다. 아래 `V22 REGRESSION LOCK`, `V21 REGRESSION LOCK`, `AUTONOMOUS FULL-RUN LOCK`, `PHASE COCKPIT LOCK`, `DIRECT-INGEST GOLD LOCK`, `GOLD-RUN HARD GUARD`보다 우선한다.

핵심 원칙은 다음 한 줄이다.

```text
오염된 연구를 살리는 것보다, 처음부터 outcome snapshot byte를 BLIND seal 전에는 절대 만지지 않는 것이 더 중요하다.
```

20241209류 실패는 `OUTCOME_SNAPSHOT_ACCESSED_BEFORE_BLIND_SEAL`가 fatal blocker로 남았고 `direct_brain_ingest_ready=false`가 되었다. 이런 번들은 감사용으로는 가치가 있어도 brain import 대상이 아니다. v23의 목적은 이런 QUARANTINE을 잘 만드는 것이 아니라, 해당 오염 자체가 발생하지 않도록 실행 순서를 기계적으로 잠그는 것이다.

## V23.0 우선순위

다음 우선순위를 따른다.

```text
1. pre-seal outcome access 0건 유지
2. BLIND packet 물리적 저장·해시·seal receipt 생성
3. seal receipt 검증 후에만 outcome open
4. full postmortem·brain_delta 생성
5. renderer/validator 독립 검증
```

다음 문장은 실행 중 스스로에게 허용되지 않는다.

```text
outcome 파일을 미리 받아만 두고 안 보면 괜찮다
sha256만 계산했으니 오염이 아니다
row_count만 확인했으니 괜찮다
blind/outcome을 같은 loop에서 받아놓고 BLIND에는 안 쓰면 된다
나중에 clean-room attempt로 수리하면 된다
```

v23에서는 **accepted attempt 기준**으로 아래 값이 모두 0이어야 한다.

```text
preseal_outcome_download_count == 0
preseal_outcome_stat_count == 0
preseal_outcome_byte_size_count == 0
preseal_outcome_sha256_count == 0
preseal_outcome_header_read_count == 0
preseal_outcome_row_count_count == 0
preseal_outcome_parse_count == 0
preseal_outcome_sample_print_count == 0
preseal_outcome_label_calculation_count == 0
preseal_outcome_winner_census_count == 0
preseal_outcome_used_in_blind_graph_count == 0
```

`access JSON`에 들어 있는 다음 값은 routing metadata이므로 허용된다.

```text
outcome_snapshot_path 문자열
outcome_snapshot_sha256 expected 문자열
outcome_snapshot_row_count expected 숫자
outcome_snapshot_bytes expected 숫자
```

단, 이 metadata를 이용해 실제 outcome snapshot 파일을 다운로드·stat·hash·open하는 것은 PHASE_2 seal receipt 검증 전에는 금지다.

GitHub outcome blob existence page를 열었지만 Raw CSV rows/header/sample/byte/hash/row_count/label을 보지 않은 경우는 PRESEAL_OUTCOME_CONTENT_ACCESS가 아니며, acquisition_warnings에 OUTCOME_BLOB_EXISTENCE_PAGE_OPENED_WITHOUT_RAW_CONTENT로 기록하고 ACCEPT_FULL 가능성을 유지한다.

## V23.1 accepted attempt에서 outcome 파일 생성 금지

PHASE_2 전에는 작업 디렉터리 안에 outcome snapshot 파일이 새로 생기면 안 된다.

금지 파일명 예:

```text
outcome_snapshot_YYYYMMDD.csv
price_YYYYMMDD_outcome.csv
YYYYMMDD_outcome.csv
research_daily_outcome_YYYYMMDD.csv
atlas/research_daily/snapshots/YYYY/MM/YYYYMMDD.csv 로컬 복사본
```

PHASE_0/PHASE_1/PHASE_2에서 허용되는 가격 파일은 오직 다음뿐이다.

```text
blind_snapshot_YYYYMMDD.csv
research_daily_access_YYYYMMDD.json
research_daily_manifest.json
research_daily_schema.json
trading_calendar.csv
```

`access_log.jsonl`에 `logical_role=outcome_snapshot` record가 PHASE_3 전에 있으면 `ACCEPT_FULL` 금지다. PHASE_3 이전에는 outcome_snapshot logical_role 자체가 등장하지 않아야 한다.

## V23.2 도구 호출 전 정적 preflight 필수

PHASE_2 전에는 shell/Python 코드를 실행하기 전에 코드 문자열을 정적으로 검사한다.

다음 패턴이 PHASE_2 전 코드에 있으면 **실행하지 말고 코드를 고친다.**

```text
outcome_snapshot
outcome_snapshot_path를 download/stat/open/read_csv/sha256sum/wc/head/tail/grep/find에 사용
pd.read_csv(...outcome...)
csv.DictReader(open(...outcome...))
sha256sum blind_snapshot_... outcome_snapshot_...
for path in [blind_snapshot_path, outcome_snapshot_path]
for p in snapshot_paths where snapshot_paths contains outcome
for name in ['blind', 'outcome']
upper_limit_touched
upper_limit_closed
high_return_pct
close_return_pct
high_return_rank
winner census
h20
upper touched / closed / h20
```

허용되는 예외는 access JSON을 파싱해 metadata ledger에 문자열을 저장하는 경우뿐이다. 이때도 파일 다운로드·stat·hash·header·row_count는 하지 않는다.

PHASE_2 전 preflight result는 `phase_state.json`과 `access_log.jsonl`에 남긴다.

```json
{
  "phase": "PHASE_1_BUILD_BLIND_ONLY",
  "preflight_outcome_access_banned_patterns_found": 0,
  "outcome_file_download_allowed": false,
  "outcome_file_stat_allowed": false,
  "outcome_file_hash_allowed": false,
  "outcome_file_parse_allowed": false
}
```

## V23.3 작업 디렉터리 분리

실행자는 날짜별 fresh workspace를 만든다.

```text
work_root = /mnt/data/nslab_run_<YYYYMMDD>_<input_sha8>/
blind_work_dir = work_root/blind_only/
postseal_work_dir = work_root/postseal_outcome/
preseal_quarantine_dir = work_root/preseal_quarantine/
```

규칙:

```text
PHASE_0/1/2에서는 postseal_work_dir를 만들지 않는다.
PHASE_0/1/2에서는 outcome_snapshot 파일명을 가진 파일을 어떤 디렉터리에도 만들지 않는다.
PHASE_2 seal receipt 검증 뒤에만 postseal_work_dir를 생성한다.
PHASE_3에서만 outcome snapshot을 postseal_work_dir 아래에 저장한다.
```

기존 `/mnt/data`에 같은 날짜 outcome 파일이 있으면 파일 내용을 읽지 말고 이름·경로만 보고 `preseal_quarantine_dir`로 이동한다. 이동 후에도 sha256, byte_size, header, row_count를 계산하지 않는다. 기존 잔존 파일의 내용은 accepted attempt의 어떤 graph에도 들어가지 않는다.

## V23.4 pre-seal outcome 접촉 발생 시 처리

pre-seal outcome 접촉을 두 종류로 나눈다.

### V23.4-A byte/content 접촉 없는 filename-only 잔존물 처리

다음은 오염이 아니다.

```text
작업 시작 전에 /mnt/data에 outcome처럼 보이는 파일명이 존재함
파일 내용을 읽지 않고 filename/path만 보고 quarantine dir로 이동함
```

이 경우 accepted attempt는 계속 가능하다. 단, `access_log.jsonl`에는 `PREEXISTING_OUTCOME_FILENAME_QUARANTINED_WITHOUT_READ`로 기록하고, 아래 값은 여전히 0이어야 한다.

```text
preseal_outcome_stat_count
preseal_outcome_sha256_count
preseal_outcome_header_read_count
preseal_outcome_row_count_count
preseal_outcome_parse_count
preseal_outcome_label_calculation_count
```

### V23.4-B accepted attempt에서 outcome byte 접촉 발생

다음 중 하나라도 발생하면 해당 attempt는 accepted attempt가 될 수 없다.

```text
BLIND seal 전에 outcome 파일 다운로드
BLIND seal 전에 outcome 파일 stat/byte_size 계산
BLIND seal 전에 outcome 파일 sha256 계산
BLIND seal 전에 outcome 파일 header 읽기
BLIND seal 전에 outcome 파일 row_count 계산
BLIND seal 전에 outcome 파일 parse
BLIND seal 전에 outcome 파일 sample 출력
BLIND seal 전에 outcome price label 계산
BLIND seal 전에 winner census 계산
```

v23에서는 이것을 “metadata-only recoverable”로 accepted attempt 안에서 살리지 않는다.

처리:

```text
accepted_attempt_valid = false
bundle_status = QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
blind_valid = false
forecast_evaluation_eligible = false
brain_eligible = false
direct_brain_ingest_ready = false
training_eligible_record_count = 0
fatal_blockers += [PRESEAL_OUTCOME_BYTE_ACCESS]
ACCEPT_FULL 금지
```

단, 이때 긴 사후 연구를 만들지 않는다. 최소 audit bundle만 생성한다.

허용되는 최소 audit bundle:

```text
schema_version: nslab.research_bundle.v11
artifact_type: research_episode_bundle
bundle_status: QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
brain_eligible: false
direct_brain_ingest_ready: false
final_watchlist_size: 0
outcome_ledger_count: 0
brain_delta_count: 0
fatal_blockers: [PRESEAL_OUTCOME_BYTE_ACCESS]
phase_state.json
access_log.jsonl
acquisition_warnings.jsonl
validation_report.json
```

금지:

```text
오염된 상태에서 final_watchlist 20개 작성
오염된 상태에서 outcome_ledger 2627개 작성
오염된 상태에서 postmortem summary 작성
오염된 상태에서 brain_delta 100개 작성
오염된 상태에서 정치테마 thesis를 사후 재구성
```

즉 오염된 연구는 “쓸 수 없는 연구”다. 감사 로그만 남기고, brain import-ready 연구는 깨끗한 새 실행에서만 만든다.

## V23.5 clean run의 성공 조건

`ACCEPT_FULL`에서 반드시 다음이 true여야 한다.

```text
phase_state.accepted_attempt_valid == true
phase_state.blind_sealed == true
phase_state.outcome_access_allowed == true only after blind_sealed
access_log_preseal_outcome_logical_role_count == 0
preseal_outcome_download_count == 0
preseal_outcome_stat_count == 0
preseal_outcome_sha256_count == 0
preseal_outcome_header_read_count == 0
preseal_outcome_row_count_count == 0
preseal_outcome_parse_count == 0
preseal_outcome_label_calculation_count == 0
postseal_outcome_download_count == 1
postseal_outcome_parse_count == 1
postseal_outcome_sha256_matches_access == true
```

`blind_report.md`에 “D snapshot 다운로드 0”이라고 썼더라도 `access_log.jsonl` 계산값이 다르면 access_log를 진실로 본다.

## V23.6 start cursor 대체 금지 강화

사용자가 `이번 세션 시작 파일: news_YYYYMMDD.csv`를 지정한 경우, 그 파일이 docs/csv에 없으면 다음 날짜 파일로 넘어가 연구하지 않는다.

처리:

```text
bundle_status = ACQUIRE_FAILED_START_CURSOR_ABSENT
brain_eligible = false
direct_brain_ingest_ready = false
outcome_research_performed = false
selected_input_file = null
fatal_blockers = [START_CURSOR_FILE_ABSENT]
```

단, 이 경우에도 실제로 다음을 확인한 후에만 실패를 선언한다.

```text
GitHub blob page 확인
Raw endpoint 확인
download fallback 확인
contents API 또는 codeload ZIP fallback 확인
```

다른 날짜 CSV로 대체하지 않는다. “next existing sorted CSV”는 사용자가 명시적으로 순차 실행을 허용했을 때만 가능하며, 시작 파일 자체가 존재하지 않는 상황에서는 자동 대체하지 않는다.

## V23.7 validator hard checks

`validation_report.json`에는 다음 critical check가 반드시 actual/expected object로 들어간다.

```text
zero_preseal_outcome_access_verified
accepted_attempt_preseal_outcome_download_count_zero
accepted_attempt_preseal_outcome_stat_count_zero
accepted_attempt_preseal_outcome_sha256_count_zero
accepted_attempt_preseal_outcome_header_read_count_zero
accepted_attempt_preseal_outcome_row_count_count_zero
accepted_attempt_preseal_outcome_parse_count_zero
accepted_attempt_preseal_outcome_label_count_zero
access_log_preseal_outcome_logical_role_count_zero
postseal_outcome_access_after_blind_seal_verified
blind_report_access_log_parity_verified
start_cursor_exact_file_verified
no_next_existing_csv_substitution_verified
```

각 check는 다음 형태를 가진다.

```json
{
  "check_id": "zero_preseal_outcome_access_verified",
  "actual": true,
  "expected": true,
  "expected_source": "PROMPT_CONSTANT",
  "passed": true,
  "severity": "critical",
  "error_ids": []
}
```

다음 중 하나라도 true이면 `ACCEPT_FULL` 금지다.

```text
preseal_outcome_download_count > 0
preseal_outcome_stat_count > 0
preseal_outcome_sha256_count > 0
preseal_outcome_header_read_count > 0
preseal_outcome_row_count_count > 0
preseal_outcome_parse_count > 0
preseal_outcome_label_calculation_count > 0
access_log_preseal_outcome_logical_role_count > 0
start_cursor_file_absent == true and selected_next_existing_csv == true
```

## V23.8 프롬프트 내부 충돌 해결

이 파일의 다른 섹션이 다음처럼 말하더라도 v23이 우선한다.

```text
metadata-only preseal outcome touch는 recoverable이다
outcome 파일을 미리 다운로드했지만 읽지 않았으면 clean attempt로 복구 가능하다
QUARANTINE은 최대한 피한다
```

v23 해석:

```text
accepted attempt에서는 preseal outcome byte 접촉 0건만 허용한다.
preseal outcome byte 접촉이 생긴 실행은 brain import 금지다.
clean research는 오염 없는 새 실행에서만 만든다.
```


────────────────────────────────────────
V22 REGRESSION LOCK — 0622 구조 레퍼런스·ledger parity·outcome→news 감사
────────────────────────────────────────

이 섹션은 20241202류 회귀와 “상한가 → 뉴스” 사후 감사 누락을 막기 위한 최상위 추가 게이트다. 아래 `V21 REGRESSION LOCK`, `AUTONOMOUS FULL-RUN LOCK`, `PHASE COCKPIT LOCK`, `DIRECT-INGEST GOLD LOCK`, `GOLD-RUN HARD GUARD`보다 우선한다.

핵심 방향은 다음 한 줄이다.

```text
20260622 gold example은 따라 써도 되는 문체·구조의 기준점이지만, 숫자·종목·rank·score를 복제하지 말고 이번 D의 입력과 outcome에서 계산한 record 장부로만 채운다.
```

## V22.0 Gold reference anti-overfit rule

가능하면 다음 파일을 구조 reference로 먼저 읽는다.

```text
gold_reference_primary = docs/example.md
gold_reference_fallback = 20260622_nslab_episode_bundle.example.md
```

이 reference에서 참고할 수 있는 것은 다음뿐이다.

```text
front matter shape
BLIND/POSTMORTEM 물리적 분리 방식
logical block 구성
row-level source ledger 방식
brain_delta supervised record 방식
validation_report check object 방식
renderer/validator provenance 방식
outcome-only relation을 수혜관계로 소급하지 않는 태도
```

다음은 절대 복사하지 않는다.

```text
0622의 row_count, source_ledger_count, brain_delta_count 같은 숫자
0622의 종목명, sector, final_watchlist, rank, score
0622의 특정 실패·성공 결론
0622의 candidate pool 크기
0622의 outcome 분포
```

validator hard check:

```text
gold_reference_loaded_or_declared_unavailable == true
gold_reference_usage_scope == STRUCTURE_ONLY
gold_reference_numeric_copy_count == 0
gold_reference_candidate_copy_count == 0
gold_reference_overfit_count == 0
```

`gold_reference_loaded_or_declared_unavailable`가 false이면 warning이 아니라 critical이다. 단, reference 파일을 도구 제약으로 못 읽었을 경우에는 `acquisition_warnings.jsonl`에 남기고, 이 프롬프트에 명시된 gold-shape 상수로 대체한다.

## V22.1 Source / fact / inference ledger population parity gate

`ACCEPT_FULL`은 final 후보 20개만 그럴듯하게 쓰는 파일이 아니다. 전체 연구 장부가 import 가능한 밀도로 닫혀야 한다.

### V22.1-A source_ledger row-level coverage

`source_ledger.jsonl`은 파일 단위 source만 담으면 부족하다. 정상 거래일 `ACCEPT_FULL`에서는 입력 CSV의 모든 row에 대해 row-level source record를 만든다.

각 news row source record 최소 필드:

```json
{
  "source_id": "SRC-NEWS-000001",
  "source_type": "news_csv_row",
  "row_id": "NEWS-000001",
  "input_file": "news_YYYYMMDD.csv",
  "title": "원문 제목",
  "published_at": "YYYY-MM-DDTHH:MM:SS+09:00",
  "available_before_cutoff": true,
  "time_verified": true,
  "content_sha256": "row title+body hash",
  "body_missing": false,
  "usage_phase": "BLIND",
  "notes": "source ledger에는 본문을 중복 복제하지 않고 row_id/hash로 추적한다"
}
```

필수 source coverage:

```text
source_ledger_news_row_count == news_row_count
source_ledger_core_file_source_count >= 6
source_ledger_count >= news_row_count + source_ledger_core_file_source_count
source_ledger_missing_news_row_count == 0
source_ledger_duplicate_news_row_source_id_count == 0
```

core file sources:

```text
SRC-MAIN-PROMPT
SRC-NEWS-CSV
SRC-RESEARCH-DAILY-MANIFEST
SRC-RESEARCH-DAILY-SCHEMA
SRC-TRADING-CALENDAR
SRC-ACCESS-JSON
SRC-BLIND-SNAPSHOT
SRC-OUTCOME-SNAPSHOT(post-seal only)
```

### V22.1-B fact ledger parity

`fact_ledger_blind.jsonl`은 final 후보만을 위한 장식 장부가 아니다. Issuer Entity Gate를 통과하고 `candidate_screening.jsonl`에 오른 observation은 최소 하나의 primary fact 또는 명시적 no-fact rejection reason을 가져야 한다.

허용 구조:

```text
candidate_screening.source_fact_ids_count >= 1
또는
candidate_screening.no_fact_rejection_reason in [
  NOT_ISSUER_SCOPED,
  MARKET_FLOW_TABLE_ONLY,
  BODY_TABLE_CONTEXT_ONLY,
  COMMON_NOUN_OR_HOMONYM,
  ATTENDEE_LIST_ONLY,
  MANUFACTURER_ONLY,
  INVESTOR_HOLDING_ONLY,
  P_SNAPSHOT_ONLY,
  DUPLICATE_EVENT,
  ROUTINE_ADMIN_NOTICE_ONLY
]
```

정상 `ACCEPT_FULL` hard checks:

```text
candidate_screening_count == issuer_observation_count
candidate_screening_unlinked_to_fact_or_rejection_count == 0
final_candidate_primary_fact_missing_count == 0
watch_secondary_primary_fact_or_rejection_missing_count == 0
exclude_primary_fact_or_rejection_missing_count == 0
fact_exact_quote_found_count == fact_ledger_count
fact_offset_mismatch_count == 0
fact_source_id_missing_count == 0
fact_row_id_missing_in_source_ledger_count == 0
```

### V22.1-C inference / mechanism linkage parity

final candidate의 `economic_mechanism`은 산문 템플릿이 아니라 fact에 붙은 검증된 inference여야 한다.

각 final item은 다음 중 하나를 반드시 가진다.

```text
supporting_inference_ids_count >= 1
또는
mechanism_in_fact_id_count >= 1 and inference_exempt_reason == MECHANISM_DIRECTLY_STATED_IN_FACT
```

`inference_ledger_blind.jsonl` 최소 필드:

```text
inference_id
statement
inference_type
supporting_fact_ids
scope_event_ids
scope_entity_ids
mechanism_target
uncertainty
verifier_decision
verifier_reason
```

검증 규칙:

```text
final_supported_inference_or_exempt_count == final_watchlist_count
final_unsupported_mechanism_count == 0
inference_supporting_fact_missing_count == 0
inference_cross_event_leak_count == 0
inference_cross_issuer_leak_count == 0
mechanism_template_only_count == 0
```

### V22.1-D ledger population audit block

최종 bundle은 다음 block을 반드시 포함한다.

```text
<!-- NSLAB:BEGIN ledger_population_audit.json -->
...
<!-- NSLAB:END ledger_population_audit.json -->
```

스키마 최소 필드:

```json
{
  "schema_version": "nslab.ledger_population_audit.v1",
  "news_row_count": 0,
  "source_ledger_news_row_count": 0,
  "source_ledger_core_file_source_count": 0,
  "issuer_observation_count": 0,
  "candidate_screening_count": 0,
  "fact_ledger_count": 0,
  "inference_ledger_count": 0,
  "candidate_screening_unlinked_to_fact_or_rejection_count": 0,
  "final_supported_inference_or_exempt_count": 0,
  "final_watchlist_count": 0,
  "checks": {
    "source_ledger_row_coverage_verified": true,
    "fact_screening_population_parity_verified": true,
    "inference_mechanism_linkage_verified": true,
    "source_fact_id_closure_verified": true
  },
  "fatal_blockers": []
}
```

위 `fatal_blockers`가 비어 있지 않으면 `direct_brain_ingest_ready=false`다.

## V22.2 Outcome→News post-seal audit gate

POSTMORTEM은 final 후보 성과만 보는 것이 아니다. 거래일 D의 상한가·고가 leaders를 outcome에서 먼저 찾고, 그 ticker를 cutoff 이전 뉴스 입력으로 역추적해야 한다.

이 감사는 반드시 `PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL` 이후에만 수행한다. BLIND를 수정하는 데 사용하지 않는다.

### V22.2-A outcome leader cohort

다음 집합을 모두 만든다.

```text
upper_limit_touched_leaders
upper_limit_closed_leaders
high_return_ge_20_leaders
high_return_top_30_clean_leaders
final_watchlist_outcome_join
```

중복 ticker는 하나의 `outcome_leader_id`로 합친다.

각 leader record 최소 필드:

```json
{
  "outcome_leader_id": "OL-000001",
  "ticker": "000000",
  "name_on_D": "회사명",
  "market": "KOSDAQ",
  "response_class": "LIMIT_UP_CLOSED | LIMIT_UP_TOUCHED | HIGH20 | HIGH10 | ...",
  "high_return_pct": 0.0,
  "close_return_pct": 0.0,
  "upper_limit_touched": false,
  "upper_limit_closed": false,
  "label_quality": "verified | quarantined",
  "outcome_source_id": "SRC-RD-OUTCOME-YYYYMMDD"
}
```

### V22.2-B cutoff news reverse search

각 outcome leader에 대해 입력 CSV 안에서 다음 순서로 검색한다.

```text
1. ticker literal exact match
2. blind/outcome snapshot의 company name exact match
3. accepted entity alias exact match
4. body table/list extractor 결과
5. sealed theme universe의 cutoff-before member edge
6. market_state / continuation pool의 P snapshot-only edge
```

금지:

```text
사후 웹검색 결과로 cutoff row를 만든 척하기
결과에서 함께 올랐다는 이유만으로 같은 테마 edge 생성
fuzzy substring만으로 hit 처리
회사명 아닌 일반어를 ticker hit로 처리
input_news_hits가 없는데 direct catalyst를 생성
```

### V22.2-C outcome_to_news_audit.jsonl 필수

최종 bundle에는 다음 block을 반드시 포함한다.

```text
<!-- NSLAB:BEGIN outcome_to_news_audit.jsonl -->
...
<!-- NSLAB:END outcome_to_news_audit.jsonl -->
```

각 record 최소 필드:

```json
{
  "audit_id": "OTN-000001",
  "outcome_leader_id": "OL-000001",
  "ticker": "000000",
  "name_on_D": "회사명",
  "outcome_response_class": "LIMIT_UP_CLOSED",
  "input_news_hit_status": "DIRECT_TICKER_HIT | DIRECT_NAME_HIT | BODY_TABLE_HIT | SEALED_THEME_MEMBER | CONTINUATION_POOL_ONLY | NO_INPUT_NEWS_HIT | UNRESOLVED_NAME_COLLISION",
  "matched_row_ids": [],
  "matched_source_ids": [],
  "matched_fact_ids": [],
  "matched_observation_ids": [],
  "matched_screening_ids": [],
  "was_in_final_watchlist": false,
  "was_in_candidate_screening": false,
  "was_in_continuation_pool": false,
  "postmortem_classification": "BLIND_FINAL_HIT | RANKING_MISS | CANDIDATE_GENERATION_MISS | SCREENED_OUT_BUT_WINNER | CONTINUATION_POOL_HIT | NEWSLESS_OR_UNEXPLAINED | RETROSPECTIVE_THEME_ONLY | PRICE_MEMORY_ONLY | LABEL_QUARANTINED",
  "training_eligible": false,
  "training_eligibility_reason": "",
  "no_hallucinated_catalyst": true
}
```

### V22.2-D outcome leader census block

최종 bundle에는 다음 block을 반드시 포함한다.

```text
<!-- NSLAB:BEGIN outcome_leader_census.jsonl -->
...
<!-- NSLAB:END outcome_leader_census.jsonl -->
```

`outcome_leader_census.jsonl`과 `outcome_to_news_audit.jsonl`은 1:1이어야 한다.

```text
outcome_leader_census_count == outcome_to_news_audit_count
outcome_to_news_missing_audit_count == 0
outcome_to_news_duplicate_audit_count == 0
```

### V22.2-E classification policy

분류 규칙:

```text
was_in_final_watchlist == true and response strong
→ BLIND_FINAL_HIT

was_in_candidate_screening == true and was_in_final_watchlist == false and response strong
→ RANKING_MISS 또는 SCREENED_OUT_BUT_WINNER

input_news_hit_status in [DIRECT_TICKER_HIT, DIRECT_NAME_HIT, BODY_TABLE_HIT]
and was_in_candidate_screening == false
→ CANDIDATE_GENERATION_MISS

input_news_hit_status == CONTINUATION_POOL_ONLY
→ CONTINUATION_POOL_HIT 또는 PRICE_MEMORY_ONLY

input_news_hit_status == NO_INPUT_NEWS_HIT
→ NEWSLESS_OR_UNEXPLAINED

input_news_hit_status == RETROSPECTIVE_THEME_ONLY
→ training_eligible=false
```

중요:

```text
NEWSLESS_OR_UNEXPLAINED는 실패가 아니라 중요한 학습 record다.
NO_INPUT_NEWS_HIT인 상한가에 억지 catalyst를 만들지 않는다.
input_news_hits가 있었는데 놓친 경우와 애초에 뉴스가 없던 경우를 반드시 구분한다.
```

validator hard check:

```text
outcome_to_news_audit_executed == true
outcome_leader_census_count >= upper_limit_touched_count
outcome_to_news_audit_count == outcome_leader_census_count
outcome_to_news_missing_audit_count == 0
outcome_to_news_hallucinated_catalyst_count == 0
outcome_to_news_direct_hit_without_source_id_count == 0
outcome_to_news_no_input_news_hit_with_direct_catalyst_count == 0
candidate_generation_error_case_count >= outcome_to_news_candidate_generation_miss_count
newsless_or_unexplained_case_count >= outcome_to_news_newsless_count
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_OUTCOME_TO_NEWS_AUDIT_INCOMPLETE
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V22.3 validation_report actual/expected object hard gate

`validation_report.json`의 checks는 문자열 리스트가 아니다. 각 check는 반드시 object다.

허용 스키마:

```json
{
  "checks": {
    "check_id": {
      "check_id": "check_id",
      "passed": true,
      "actual": {},
      "expected": {},
      "expected_source": "PROMPT_CONSTANT | INPUT_PARSE | ACCESS_JSON | CANONICAL_GRAPH_PRESEAL | CANONICAL_GRAPH_POSTSEAL | GOLD_SHAPE_CONSTANT",
      "severity": "critical | warning",
      "error_ids": []
    }
  }
}
```

금지:

```text
checks: ["brain_delta_density_verified", "source_ledger_ok"]
actual 누락
expected 누락
expected_source 누락
passed만 true로 나열
validation_report check와 direct_ingest_contract hard_gate_summary 불일치
```

V22 필수 check_id:

```text
gold_reference_structure_only_verified
source_ledger_row_coverage_verified
fact_screening_population_parity_verified
inference_mechanism_linkage_verified
ledger_population_audit_verified
outcome_leader_census_verified
outcome_to_news_audit_executed
outcome_to_news_audit_coverage_verified
outcome_to_news_no_hallucinated_catalyst_verified
validation_check_object_schema_verified
direct_ingest_contract_validation_parity_verified
```

하나라도 없거나 object schema를 만족하지 않으면:

```text
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATION_REPORT_NOT_STRUCTURED
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V22.4 direct_ingest_contract count/hash parity 확장

`direct_ingest_contract.json.record_import_manifest`에는 기존 count 외에 다음을 추가한다.

```json
{
  "source_ledger_news_row_count": 0,
  "outcome_leader_census_count": 0,
  "outcome_to_news_audit_count": 0,
  "ledger_population_audit_sha256": "",
  "outcome_to_news_audit_sha256": "",
  "outcome_leader_census_sha256": "",
  "validation_check_object_schema_verified": true
}
```

`direct_brain_ingest_ready=true`인 경우 위 값은 실제 artifact parse 결과와 일치해야 한다.

## V22.5 final response and importer safety

최종 채팅 응답이 한 줄이어야 해도 내부 bundle은 V22 block을 모두 포함해야 한다. `outcome_to_news_audit.jsonl`과 `ledger_population_audit.json`가 없으면 파일이 아무리 보기 좋아도 importer-ready가 아니다.

요약:

```text
0622는 구조 reference다.
이번 D의 입력은 이번 D의 full parse로만 결정한다.
source_ledger는 모든 news row를 추적한다.
fact/inference는 screening/final mechanism과 닫혀야 한다.
outcome leader는 post-seal 후 반드시 input news로 역추적한다.
없으면 newsless로 남기고, 억지 catalyst를 만들지 않는다.
validation_report는 actual/expected object로 계산 증거를 남긴다.
```


────────────────────────────────────────
V21 REGRESSION LOCK — brain_delta 밀도·market_state override audit·body extractor
────────────────────────────────────────

이 섹션은 20241216류 회귀를 막기 위한 최상위 추가 게이트다. 아래 본문, `AUTONOMOUS FULL-RUN LOCK`, `PHASE COCKPIT LOCK`, `DIRECT-INGEST GOLD LOCK`, `GOLD-RUN HARD GUARD`보다 우선한다.

해당 회귀의 형태는 다음과 같다.

```text
phase/snapshot/schema는 정상으로 보이지만,
brain_delta가 record-level 모집단 대비 과소 생성되고,
POLITICAL_THEME_CONTINUATION_MARKET_STATE·투자경고·매매거래정지·단기과열 같은 market_state 후보가
직접 issuer event보다 상위 final rank를 과도하게 차지한다.
```

v21의 원칙:

```text
phase가 깨끗한 것만으로 ACCEPT_FULL이 아니다.
semantic type이 맞는 것만으로 ACCEPT_FULL이 아니다.
ACCEPT_FULL은 record 밀도와 final ranking 품질까지 통과해야 한다.
```

## V21.1 Brain Delta density hard gate

`brain_delta.jsonl`은 요약 교훈이 아니라 import 대상 record 모집단이다.
정상 거래일이고 `research_daily` outcome snapshot이 존재하면 `expected_brain_delta_min`은 반드시 canonical_graph count에서 계산한다.

허용 formula:

```text
expected_brain_delta_min = max(
    100,
    issuer_day_case_count
  + supervised_direct_event_case_count
  + theme_formation_case_count
  + blind_leader_pair_count
)
  + candidate_generation_error_case_count
  + ranking_error_case_count
  + newsless_or_unexplained_case_count
  + negative_control_case_count
```

아직 error/correction population을 별도 count로 계산하지 않는 구현이라도 최소한 아래 보수식을 사용한다.

```text
expected_brain_delta_min_conservative = max(
    100,
    issuer_day_case_count
  + direct_event_case_count
  + theme_formation_case_count
  + blind_leader_pair_count
)
```

정상 거래일에서는 아래 hard floor도 반드시 적용한다.

```text
if research_daily_access_verified == true
and outcome_ledger_count > 0
and direct_event_case_count >= 50:
    brain_delta_record_count >= expected_brain_delta_min_conservative
```

금지:

```text
expected_brain_delta_min = 7
expected_brain_delta_min = 실제 생성된 brain_delta 개수
brain_delta_record_count가 20~40개인데 issuer_day/direct_event 모집단이 100개 이상인 ACCEPT_FULL
brain_delta_summary만 있고 supervised_issuer_day_case / supervised_direct_event_case record가 부족한 ACCEPT_FULL
```

validator hard check:

```text
brain_delta_density_verified == true
brain_delta_record_count >= expected_brain_delta_min 또는 expected_brain_delta_min_conservative
brain_delta_count_by_type.supervised_issuer_day_case == issuer_day_case_count
brain_delta_count_by_type.supervised_direct_event_case == direct_event_case_count
brain_delta_count_by_type.blind_leader_preference_pair == blind_leader_pair_count
brain_delta_expected_source == CANONICAL_GRAPH_PRESEAL_OR_POSTSEAL_COUNTS
brain_delta_expected_source != GENERATED_OUTPUT
```

위반 시:

```text
critical_error_count += 1
brain_delta_underfilled_count += 1
bundle_status = QUARANTINE_BRAIN_DELTA_UNDERFILLED
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V21.2 market_state continuation lane + fatigue override gate

`MARKET_STATE_CONTINUATION`, `POLITICAL_THEME_CONTINUATION_MARKET_STATE`, 투자주의, 투자경고, 투자위험, 매매거래정지, 거래재개, 단기과열, 조회공시 요구, 시황변동 답변 없음은 **운영 사건이 아니라 시장상태 신호**다.

시장상태 신호는 국장에서 실제로 강하게 작동할 수 있다. 따라서 market_state 후보를 일괄 금지하거나, 기계적 수량 제한만으로 final에서 제거하지 않는다.

다만 market_state 신호는 수주·계약·제품·허가·상용화·자본정책 같은 direct issuer catalyst와 같은 종류의 신호가 아니다. 따라서 final에 올릴 경우 반드시 별도 lane과 별도 audit로 처리한다.

market_state 신호의 허용 용도:

```text
MARKET_MEMORY_CONTINUATION lane
continuation_pool
risk/fatigue audit
market_state_or_continuation brain_delta
ranking_error_case
negative_control_case
```

금지:

```text
market_state 신호를 CONTRACT_ORDER로 재해석
market_state 신호를 PRODUCT_COMMERCIALIZATION으로 재해석
market_state 신호를 BIO_STAGE_ADVANCE로 재해석
market_state 신호를 CAPITAL_POLICY로 재해석
P snapshot-only 이유를 direct issuer fact로 둔갑
투자경고/정지/조회공시 문구를 매출·마진·허가확률 변화로 설명
```

### V21.2-A market_state lane 분리

market_state-only 후보는 `final_watchlist`에 들어갈 수 있다. 단, 다음 필드를 반드시 가진다.

```json
{
  "candidate_lane": "MARKET_MEMORY_CONTINUATION",
  "material_fact_class": "MARKET_STATE_CONTINUATION | POLITICAL_THEME_CONTINUATION_MARKET_STATE | INVESTMENT_WARNING | TRADING_HALT_NOTICE | SHORT_TERM_OVERHEAT | PRICE_QUERY_NOTICE",
  "catalyst_type": "MARKET_MEMORY_CONTINUATION",
  "market_state_signal_type": "...",
  "fatigue_risk_score": 0,
  "continuation_strength_score": 0,
  "market_state_final_eligible": true,
  "market_state_override_required": false,
  "market_state_override_id": null
}
```

시장상태 후보는 `quality_A_direct_event` 또는 `quality_B_direct_event`와 비교될 때 반드시 leader-pair를 남긴다.

```text
market_state_vs_direct_leader_pair_required == true
```

### V21.2-B fatigue risk 계산

market_state-only 후보는 다음 중 하나 이상이면 `fatigue_risk_dominant=true` 후보로 표시한다.

```text
upper_limit_touch_count_5d >= 2
upper_limit_close_count_5d >= 2
high_return_ge_20_count_5d >= 2
high_return_ge_10_count_5d >= 4
return_5d_pct >= 50
return_10d_pct >= 100
return_20d_pct >= 150
투자위험종목 최초지정
투자경고종목 지정
투자경고종목 지정예고
매매거래정지 예고
매매거래정지 및 재개
단기과열종목 지정 또는 지정예고
현저한 시황변동 조회공시 요구인데 중요 공시대상 없음
```

`fatigue_risk_dominant=true`는 자동 탈락이 아니다. 하지만 final 상위권에 올리려면 continuation 쪽 근거가 fatigue를 이긴다는 override audit가 필요하다.

continuation strength 근거 예:

```text
cutoff CSV 안에 당일 정치·정책·테마를 새로 강화하는 뉴스가 있음
해당 ticker가 단순 P snapshot이 아니라 cutoff row에서 issuer-scoped로 언급됨
거래소 공시가 단순 경고가 아니라 거래 재개·지정 해제·불확실성 해소에 가까움
전일 과열이 있으나 종가 잠김·거래대금·회전율·호가 기억이 exhaustion보다 continuation으로 해석됨
같은 theme 안에서 direct event 후보보다 위에 둘 이유가 blind leader-pair로 명시됨
시장 전체가 해당 lane을 실제 주도 lane으로 선택할 가능성을 cutoff 이전 뉴스가 설명함
```

### V21.2-C hard cap 금지, override gate 사용

다음 수량은 **fatal hard cap이 아니다.**

```text
market_state_only_top5_count
market_state_only_top10_count
market_state_only_final_count
```

시장이 진짜 market_state/정치테마/투자주의 continuation 장이면 final에 여러 개 들어갈 수 있다. 다만 아래 기준을 넘으면 초과 후보마다 `market_state_override_audit.jsonl` record가 필수다.

```text
if market_state_only_top5_count > 1:
    each excess top5 market_state item requires override audit

if market_state_only_top10_count > 2:
    each excess top10 market_state item requires override audit

if market_state_only_final_count > 3:
    each excess final market_state item requires override audit
```

`market_state_override_audit.jsonl` 최소 필드:

```json
{
  "override_id": "MSO-...",
  "candidate_id": "CAND-...",
  "ticker": "000000",
  "name": "회사명",
  "rank": 1,
  "market_state_signal_type": "...",
  "fatigue_risk_dominant": true,
  "fatigue_risk_evidence": [],
  "continuation_strength_evidence": [],
  "fresh_context_source_fact_ids": [],
  "issuer_scoped_current_notice_fact_ids": [],
  "leader_pair_ids_vs_quality_A_direct_event": [],
  "why_continuation_not_fatigue": "...",
  "why_can_outrank_direct_event": "...",
  "semantic_verdict": "PASS | FAIL",
  "training_eligibility": "market_state_training_only | ranking_error_only | negative_control_only"
}
```

override audit가 없거나 `semantic_verdict != PASS`이면 그 후보는 `final_watchlist`에서 제거하고 `continuation_pool` 또는 `WATCH_SECONDARY`로 강등한다. 이것은 recoverable semantic error이며, 가능한 경우 canonical_graph를 수리한 뒤 ACCEPT_FULL을 계속 목표로 한다.

```text
market_state_override_missing_count > 0
→ 해당 후보 강등·재랭킹·재렌더·재검증
→ repair 가능
→ 즉시 QUARANTINE 금지
```

단, repair loop 후에도 final에 남은 excess market_state 후보의 override audit가 없으면 다음으로 처리한다.

```text
critical_error_count += 1
bundle_status = QUARANTINE_MARKET_STATE_OVERRIDE_MISSING
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

### V21.2-D 직접 issuer event 품질 tier 정의

직접 issuer event는 market_state 후보와 비교할 때 다음 tier를 사용한다.

```text
quality_A_direct_event:
  - CONTRACT_SIGNED / ORDER_RECEIVED / SUPPLY_AGREEMENT / PROJECT_AWARDED
  - REGULATORY_APPROVAL / CLINICAL_STAGE_ADVANCE / LICENSE_OR_TECH_TRANSFER_WITH_RIGHTS
  - STAKE_SALE_OR_CONTROL_CHANGE / BUYBACK / SHARE_CANCELLATION
  - source_fact_ids 존재
  - supporting_inference_ids 존재
  - final semantic audit PASS

quality_B_direct_event:
  - ANALYST_NUMERIC_EARNINGS_BRIDGE
  - PRODUCT_COMMERCIALIZATION_BY_ISSUER
  - GOVERNMENT_PROJECT_SELECTED
  - source_fact_ids 존재
  - supporting_inference_ids 존재
```

market_state-only 후보가 quality_A_direct_event보다 높은 rank에 있으면 자동 fatal이 아니다. 단, 반드시 아래가 있어야 한다.

```text
market_state_vs_quality_A_override_audit_present == true
leader_pair_ids_vs_quality_A_direct_event_count >= 1
why_can_outrank_direct_event is nonempty and candidate-specific
```

없으면 해당 market_state 후보를 quality_A direct event 아래로 강등한다.

### V21.2-E validator hard check

validator는 다음을 실제 artifact와 canonical_graph에서 다시 계산한다.

```text
market_state_lane_separation_verified == true
market_state_not_recast_as_direct_catalyst_verified == true
market_state_fatigue_audit_executed == true
market_state_override_required_count
market_state_override_present_count
market_state_override_missing_count == 0
market_state_override_semantic_fail_count == 0
market_state_excess_counts_are_not_fatal_without_override_check == true
market_state_only_outranks_quality_A_direct_event_without_override_count == 0
market_state_demoted_when_override_missing_count
```

다음은 더 이상 ACCEPT_FULL의 직접 hard cap이 아니다.

```text
market_state_only_top5_count <= 1
market_state_only_top10_count <= 2
market_state_only_final_count <= 3
```

이 수량은 report와 validation_report에 기록하되, 초과 자체는 warning이다. fatal 조건은 오직 다음이다.

```text
market_state_override_missing_count > 0
market_state_override_semantic_fail_count > 0
market_state_recast_as_direct_catalyst_count > 0
market_state_only_outranks_quality_A_direct_event_without_override_count > 0
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_MARKET_STATE_OVERRIDE_INVALID
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## V21.3 body table/list extractor hard gate

제목 중심 extractor만으로는 장전 후보 생성이 누락된다. 뉴스 CSV의 body 안에 종목 표·리스트가 있으면 전수 추출해야 한다.
특히 다음 제목·본문 패턴은 body list extractor를 반드시 실행한다.

```text
시간외 상승 종목
장전 조회상위
조회상위 Top 20
코스닥/코스피 융자잔고 증감률 상위종목
기관/외국인 순매수 상위종목
오늘의 테마
오후 이슈
관련주 목록
```

주의:

```text
body table에 나온 ticker/name은 자동 final 후보가 아니다.
issuer-scoped material fact가 없으면 final 금지다.
하지만 후보 생성 누락 검증과 candidate_generation_error_case에는 반드시 기록한다.
```

validator hard check:

```text
body_table_rows_detected_count
body_table_entities_extracted_count
body_table_candidate_generation_audit_count
body_table_final_false_promotion_count == 0
body_table_extractor_unexplained_skip_count == 0
```

`body_table_extractor_unexplained_skip_count > 0`이면 `ACCEPT_FULL` 금지다.

## V21.4 validator 필수 추가 check_id

`validation_report.json`과 `direct_ingest_contract.json`에는 다음 check_id가 실제 계산값으로 들어가야 한다.

```text
brain_delta_density_verified
brain_delta_expected_min_verified
brain_delta_type_count_parity_verified
market_state_lane_separation_verified
market_state_not_recast_as_direct_catalyst_verified
market_state_fatigue_audit_executed
market_state_override_required_count_verified
market_state_override_present_count_verified
market_state_override_missing_count_zero_verified
market_state_override_semantic_fail_count_zero_verified
market_state_excess_counts_are_not_fatal_without_override_check_verified
market_state_only_not_outrank_quality_A_direct_event_without_override_verified
body_table_extractor_executed
body_table_candidate_generation_audit_verified
gold_reference_structure_only_verified
source_ledger_row_coverage_verified
fact_screening_population_parity_verified
inference_mechanism_linkage_verified
ledger_population_audit_verified
outcome_leader_census_verified
outcome_to_news_audit_executed
outcome_to_news_audit_coverage_verified
outcome_to_news_no_hallucinated_catalyst_verified
validation_check_object_schema_verified
direct_ingest_contract_validation_parity_verified
```

위 check는 산문 선언이 아니라 validator가 artifact와 canonical_graph를 다시 읽어 계산해야 한다.
하나라도 누락되면 다음으로 처리한다.

```text
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATOR_MISSING_V21_CHECKS
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```


────────────────────────────────────────
AUTONOMOUS FULL-RUN LOCK — 조기 포기·조기 격리 금지
────────────────────────────────────────

이 섹션은 실행자의 태도를 고정하는 최상위 완주 규칙이다.
`PHASE COCKPIT LOCK`은 순서를 막고, 이 섹션은 빠른 실패 보고서·격리 skeleton으로 도망가는 행동을 막는다.

목표는 빠른 채팅 답변이 아니라, 시간이 오래 걸려도 실제로 `ACCEPT_FULL + direct_brain_ingest_ready=true`까지 도달하는 것이다.

## AF0. 기본 목표는 항상 ACCEPT_FULL이다

이번 실행의 기본 목표는 항상 다음이다.

```text
primary_target = ACCEPT_FULL
brain_eligible_target = true
direct_brain_ingest_ready_target = true
requires_manual_research_review_target = false
research_effort_budget = exhaustive_current_response_no_time_cap
quarantine_is_last_resort = true
```

다음 문장은 작업 초반의 결론으로 쓰지 않는다.

```text
완전한 연구는 실현 불가능할 수 있다
일단 격리 파일을 만들겠다
시간상 전체 파싱은 어렵다
raw가 많아서 하나하나 못 본다
JSON 다운로드가 까다로우므로 QUARANTINE으로 처리한다
20분 안에 안 끝나므로 실패 번들을 만들겠다
```

위 표현은 모든 acquisition fallback, deterministic full parse, canonical_graph repair, renderer/validator repair loop를 끝까지 수행한 뒤에도 terminal blocker가 남았을 때만 `fatal_blockers` 설명에 쓸 수 있다.

## AF1. 오래 걸리는 것은 정상이다

이 작업은 원래 오래 걸리는 전수 연구다. 실행자는 속도를 이유로 연구 범위를 줄이지 않는다.

다음을 금지한다.

```text
부분 파싱만 하고 전체 row_disposition 생략
상위 몇 행만 보고 후보 생성
price snapshot이 2600행 이상이라고 outcome ledger 생략
brain_delta를 요약 교훈 몇 개로 축소
critical validator 오류를 warning으로 낮추기
repair loop 없이 QUARANTINE으로 종료
```

반드시 수행한다.

```text
뉴스 CSV full parse
뉴스 CSV 전 행 row_disposition 생성
P blind snapshot machine parse
issuer/entity gate 통과 observation 전수 screening
final 후보마다 semantic audit
BLIND packet 실제 파일 저장·해시·봉인
seal receipt 검증
seal 이후 D outcome snapshot machine parse
full-market outcome ledger와 winner census 생성
issuer-day/direct-event/theme/pair brain_delta 생성
renderer와 validator 독립 실행
repair loop 후 최종 재검증
```

뉴스나 가격 행이 많으면 사람이 한 줄씩 정독하는 대신 parser·ledger·validator를 사용한다. “행이 많다”는 실패 사유가 아니라 machine-parse를 써야 한다는 신호다.

## AF2. recoverable과 fatal을 먼저 분리한다

오류를 발견하면 즉시 QUARANTINE으로 종료하지 말고 먼저 다음처럼 분류한다.

```text
RECOVERABLE_ACQUISITION_ERROR
- curl/urllib/socket/DNS 실패
- GitHub JSON/CSV 다운로드 방식 혼동
- MIME sniff 오판
- 파일명이 맞지만 blob HTML만 먼저 열린 상태
- GitHub blob 화면이 too large to display를 표시
처리: web/browser 또는 download fallback 재시도. ACCEPT_FULL 가능.

RECOVERABLE_RENDER_VALIDATE_ERROR
- count/hash/manifest 불일치
- marker 불일치
- renderer/validator hash 갱신 누락
- direct_ingest_contract 계산값 불일치
- cross-block input_coverage_warning 불일치
처리: canonical_graph 수정 후 재렌더·재검증. ACCEPT_FULL 가능.

RECOVERABLE_SEMANTIC_ERROR
- 특정 final 후보의 quote_role/catalyst_type 불일치
- unsupported mechanism
- generic why_now
- final filler 후보
- 단순 언급을 catalyst로 승격한 후보
처리: 해당 후보를 final에서 제거하거나 audit-only로 강등하고 재검증. ACCEPT_FULL 가능.

FATAL_PHASE_LABEL_CONTAMINATION
- BLIND seal 전에 outcome snapshot header/row_count/parse/sample/label/high_return/upper_limit 등 실제 label 또는 행 내용이 모델 context에 들어옴
- BLIND 후보·순위 생성 뒤 D outcome을 보고 BLIND를 수정함
처리: 같은 실행 ACCEPT_FULL 금지. QUARANTINE_PHASE_CONTAMINATED.
```

## AF3. pre-seal outcome 파일 접촉: v23 zero-contamination 모드

`access JSON`의 `outcome_snapshot_path`, expected sha256, expected row_count, expected bytes를 읽는 것은 routing metadata라서 허용된다.

그러나 PHASE_2 BLIND seal receipt가 검증되기 전에는 outcome snapshot 파일의 byte에 접근하지 않는다.

금지:

```text
outcome 파일 다운로드
outcome 파일 stat 또는 byte_size 계산
outcome 파일 sha256 계산
outcome 파일 header 읽기
outcome 파일 row_count 계산
outcome 파일 pandas/csv parse
outcome 파일 head/tail/grep/sample 출력
upper_limit/high_return/close_return/winner census 계산
blind_snapshot과 outcome_snapshot을 같은 loop/list에서 함께 처리
```

v23에서는 `download-only`, `hash-only`, `metadata-only file touch`를 accepted attempt 안에서 recoverable로 보지 않는다. accepted attempt의 preseal outcome byte access는 반드시 0이어야 한다.

preseal outcome byte access가 발생하면:

```text
accepted_attempt_valid = false
bundle_status = QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
blind_valid = false
forecast_evaluation_eligible = false
brain_eligible = false
direct_brain_ingest_ready = false
final_watchlist = []
outcome_ledger = []
brain_delta = []
training_eligible_record_count = 0
fatal_blockers = [PRESEAL_OUTCOME_BYTE_ACCESS]
ACCEPT_FULL 금지
```

오염된 상태에서 긴 postmortem, winner census, 정치테마 thesis, brain_delta를 만들지 않는다. 최소 audit bundle만 생성한다.

## AF4. 도구 실행 전 phase preflight를 수행한다

도구를 호출하기 전에, 실행자는 곧 실행할 shell/Python 코드가 phase를 위반하지 않는지 먼저 검사한다.
특히 PHASE_2 전 코드에 다음 패턴이 있으면 실행하지 말고 코드를 고친다.

```text
for path in [blind_snapshot_path, outcome_snapshot_path]
for name in ['blind_snapshot_...', 'outcome_snapshot_...']
sha256sum blind_snapshot_... outcome_snapshot_...
pd.read_csv(outcome_snapshot_...)
csv.DictReader(open(outcome_snapshot_...))
upper touched / closed / h20 계산
```

핵심 원칙:

```text
PHASE_2 전에는 blind_snapshot만 실제 파일로 다룬다.
PHASE_2 seal receipt가 검증되기 전에는 outcome 파일 내용을 절대 읽지 않는다.
```

## AF5. QUARANTINE skeleton 금지

진짜 fatal contamination이 아닌데 다음 형태로 끝내는 것은 금지한다.

```text
row_disposition과 후보 audit 일부만 만들고 final_watchlist를 비워 둔 QUARANTINE skeleton
brain_delta_record_count가 1~몇 개뿐인 격리 파일
validator_exit_code != 0인데 최종 산출물 생성만 하고 중단
“후보 풀은 디버깅용”이라는 이유로 연구를 끝낸 파일
```

정상 또는 recoverable 실행에서는 반드시 완전 연구 산출물로 수리한다.

```text
row_disposition_count == news_row_count
fact/inference/screening 전수 생성
final_semantic_audit 전수 생성
final_watchlist는 0~20개이되, 강한 후보가 있으면 비우지 않는다
outcome_ledger full-market 생성
brain_delta record-level population 생성
direct_ingest_contract 생성
validator_exit_code == 0까지 repair loop 수행
```

## AF6. repair loop는 실제로 수행한다

validator가 critical error를 반환하면 즉시 격리하지 않는다. 수리 가능한 오류는 canonical_graph를 고친 뒤 재렌더·재검증한다.

수리 후에는 반드시 다음 순서를 반복한다.

```text
canonical_graph 수정
→ renderer 재실행
→ validator 재실행
→ validation_report 갱신
→ bundle_manifest 갱신
→ direct_ingest_contract 갱신
→ final bundle 재조립
→ final bundle 재검증
```

repair loop는 최대 16회까지 허용한다. 각 loop는 `repair_log.jsonl`에 무엇을 고쳤는지 기록한다.
최대 횟수 안에 모든 critical check가 통과하면 `ACCEPT_FULL`이다. 최대 횟수 후에도 critical error가 남으면 그때만 `QUARANTINE_REPAIR_EXHAUSTED`다.

## AF7. 중간 질문 없이 내부 로그로 계속한다

작업 중 문제가 있으면 사용자에게 묻지 말고 다음 파일에 기록한 뒤 계속한다.

```text
acquisition_warnings.jsonl
repair_log.jsonl
phase_state.json
access_log.jsonl
validation_report.json
attempt_history.jsonl
```

최종 채팅 응답이 한 줄이어야 하더라도 내부 작업량을 줄이지 않는다. 최종 응답이 짧은 것은 출력 형식일 뿐이고, 내부 연구는 full episode bundle 기준으로 수행한다.


────────────────────────────────────────
PHASE COCKPIT LOCK — 실행자가 헤매지 않게 하는 최상단 조종석
────────────────────────────────────────

이 섹션은 실행 순서를 짧게 고정하는 최우선 차단기다. 아래의 긴 본문은 세부 규칙이고, 실제 실행자는 먼저 이 조종석을 따른다.

이번 실행의 phase_state는 오직 다음 6개만 허용한다.

```text
PHASE_0_ACQUIRE_INPUT
PHASE_1_BUILD_BLIND_ONLY
PHASE_2_BLIND_PACKET_SEALED
PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL
PHASE_4_RENDER_VALIDATE_REPAIR
PHASE_5_FINAL_ACCEPT_OR_QUARANTINE
```

## PC0. 절대 실행 순서

```text
1. PHASE_0_ACQUIRE_INPUT
   - MAIN PROMPT, 선택 news_YYYYMMDD.csv, research_daily manifest/schema/access JSON만 확보·검증한다.
   - access JSON에서는 blind_snapshot_path와 outcome_snapshot_path 문자열, expected sha256, expected row_count만 읽는다.
   - 아직 blind snapshot과 outcome snapshot을 한꺼번에 받지 않는다.

2. PHASE_1_BUILD_BLIND_ONLY
   - access.blind_snapshot_path만 다운로드·해시·parse한다.
   - 뉴스 CSV 전체를 parse하고 row_disposition을 전 행 생성한다.
   - entity/fact/inference/event/screening/final_watchlist를 완성한다.
   - final_watchlist는 0~20개만 허용한다. 통과 후보가 없으면 0개로 둔다.
   - outcome snapshot 파일은 다운로드·해시·header read·row_count·parse·sample 출력 모두 금지다.

3. PHASE_2_BLIND_PACKET_SEALED
   - blind_prediction.json, blind_report.md, row_disposition.jsonl, entity_ledger_blind.jsonl, fact_ledger_blind.jsonl, inference_ledger_blind.jsonl, candidate_screening.jsonl, blind_packet_manifest.json을 실제 파일로 저장한다.
   - 각 파일의 SHA-256을 계산한다.
   - blind_packet_manifest_sha256과 sealed_blind_report_sha256을 확정한다.
   - 이 순간 이후 BLIND namespace는 읽기 전용이다.

4. PHASE_3_OPEN_OUTCOME_ONLY_AFTER_SEAL
   - PHASE_2 seal receipt가 존재하고 검증된 뒤에만 access.outcome_snapshot_path를 다운로드한다.
   - 그 뒤에만 outcome byte_size, sha256, header, row_count, csv parse, high_return_pct, close_return_pct, upper_limit_touched를 계산한다.

5. PHASE_4_RENDER_VALIDATE_REPAIR
   - canonical_graph에서만 모든 artifact를 렌더링한다.
   - renderer와 validator는 물리적으로 독립 파일이거나 독립 entrypoint hash를 가져야 한다.
   - validator는 최종 bundle과 내부 artifact를 다시 읽어 실제 count/hash/reference를 계산한다.
   - critical error가 있으면 gate를 낮추지 말고 canonical_graph를 고친 뒤 재렌더·재검증한다.

6. PHASE_5_FINAL_ACCEPT_OR_QUARANTINE
   - validator_exit_code == 0, critical_error_count == 0, direct_brain_ingest_ready == true일 때만 ACCEPT_FULL이다.
   - critical error가 있으면 먼저 PHASE_4 repair loop로 돌려 canonical_graph를 수리한다.
   - 수리 불가능한 오염 또는 repair loop 소진이 확인된 뒤에만 명시적 QUARANTINE으로 끝낸다.
```

## PC1. pre-seal outcome hard stop — v23 zero-contamination 모드

PHASE_2_BLIND_PACKET_SEALED 전에는 outcome snapshot에 대해 다음 행동이 모두 금지다.

```text
outcome_snapshot_YYYYMMDD.csv 다운로드
price_YYYYMMDD_outcome.csv 다운로드
YYYYMMDD.csv를 outcome snapshot으로 저장
outcome snapshot stat/byte_size 계산
outcome snapshot sha256 계산
outcome snapshot header 읽기
outcome snapshot row_count 계산
outcome snapshot pandas/csv parse
outcome snapshot head/tail/grep/find/sample 출력
upper_limit_touched, upper_limit_closed, high_return_pct, close_return_pct, high_return_rank 계산
blind_snapshot과 outcome_snapshot을 같은 for-loop/list에서 함께 다운로드·검증·parse
```

PHASE_2 전 코드에서 다음 패턴이 보이면 실행하지 않고 코드를 고친다.

```text
for path in [blind_snapshot_path, outcome_snapshot_path]
for name in ['blind_snapshot_...', 'outcome_snapshot_...']
pd.read_csv(outcome_snapshot_...)
csv.DictReader(open(outcome_snapshot_...))
sha256sum blind_snapshot_... outcome_snapshot_...
upper touched / closed / h20 count를 BLIND seal 전 출력
```

v23에서는 preseal outcome 파일 byte 접촉을 recoverable로 살리지 않는다. accepted attempt에서는 preseal outcome download/stat/hash/header/row_count/parse/label count가 모두 0이어야 한다.

이미 실행해서 outcome byte에 닿았다면 다음으로 종료한다.

```text
bundle_status = QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
blind_valid = false
forecast_evaluation_eligible = false
brain_eligible = false
direct_brain_ingest_ready = false
final_watchlist_size = 0
outcome_ledger_count = 0
brain_delta_count = 0
fatal_blockers = [PRESEAL_OUTCOME_BYTE_ACCESS]
ACCEPT_FULL 금지
```

오염된 상태에서 full postmortem이나 brain_delta를 만들지 않는다. clean research는 새 오염 없는 실행에서만 만든다.

## PC2. 파일 잔존물 처리

실행 시작 시 작업 디렉터리와 `/mnt/data`에 같은 날짜의 outcome snapshot으로 보이는 파일이 있으면 먼저 감사한다.

```text
outcome_snapshot_YYYYMMDD.csv
price_YYYYMMDD_outcome.csv
YYYYMMDD_outcome.csv
research_daily/snapshots/YYYY/MM/YYYYMMDD.csv 로컬 복사본
```

이전 실행 잔존물이고 이번 실행에서 읽지 않았다면 BLIND 시작 전에 quarantine 디렉터리로 이동한다. 이번 실행에서 BLIND seal 전에 읽었거나 해시·행수·header·parse·출력했다면 `QUARANTINE_PHASE_CONTAMINATED`다.

## PC3. raw가 많다는 이유로 중단하지 않는다

뉴스 CSV와 price snapshot은 사람이 한 줄씩 읽는 문서가 아니라 machine-parse 대상이다.

```text
뉴스 CSV 전체 행:
- 전 행 row_disposition 생성
- 명시 코드·상장사명·공시/기사 유형을 parser로 전수 추출
- issuer gate 통과 행만 fact/inference/screening 심층 분석

price blind snapshot:
- PHASE_1에서 P snapshot만 machine parse
- safe D-1 feature로만 사용

price outcome snapshot:
- PHASE_3에서만 machine parse
- full-market outcome ledger와 winner census 생성
```

“행이 3000개라 하나하나 못 본다”는 말은 중단 사유가 아니다. 전수 parse와 전수 disposition/count/ledger를 만들고, 심층 의미 검토는 candidate/event 단위로 좁힌다.

## PC4. ACCEPT_FULL의 짧은 판정식

아래가 모두 true일 때만 `ACCEPT_FULL`이다.

```text
csv_full_parse_complete
research_daily_access_verified
blind_snapshot_only_used_preseal
blind_packet_sealed_before_outcome
preseal_outcome_content_access_count == 0
preseal_outcome_label_access_count == 0
preseal_outcome_download_count == 0
preseal_outcome_stat_count == 0
preseal_outcome_sha256_count == 0
preseal_outcome_header_read_count == 0
preseal_outcome_row_count_count == 0
preseal_outcome_parse_count == 0
preseal_outcome_label_calculation_count == 0
final_watchlist_size <= 20
markdown_final_watchlist_size <= 20
final_candidate_primary_fact_present
final_candidate_supported_inference_present
final_candidate_issuer_role_anchor_valid
final_candidate_catalyst_entailment_valid
weak_final_reason_zero
contextual_entity_false_positive_count == 0
final_quote_catalyst_not_entailed_count == 0
final_unsupported_mechanism_count == 0
final_filler_low_quality_count == 0
final_contextual_false_positive_count == 0
semantic_type_fixture_failure_count == 0
brain_delta_record_level
renderer_validator_independence_verified
id_source_fact_reference_closure_verified
validation_report.critical_error_count == 0
validator_exit_code == 0
direct_brain_ingest_ready == true
final_run_attempt_clean == true
discarded_attempts_not_used_in_blind_graph == true
semantic_type_firewall_fixture_all_passed
final_candidate_entity_role_allowed_verified
final_candidate_catalyst_alignment_verified
final_candidate_supported_inference_present
final_candidate_p_snapshot_only_reason_count_zero
boolean_check_strict_type_verified
text_truncation_count_zero
```

이 값들은 산문 선언이 아니라 `validate_nslab_bundle.py`가 계산한 값이어야 한다.

## PC5. phase_state와 access_log는 ACCEPT_FULL의 증거다

긴 본문을 읽고도 실행자가 phase를 헷갈리는 일을 막기 위해, 이번 실행은 내부 작업 디렉터리에 다음 두 파일을 반드시 만든다.

```text
phase_state.json
access_log.jsonl
```

`phase_state.json` 최소 필드:

```json
{
  "current_phase": "PHASE_0_ACQUIRE_INPUT",
  "phase_started_at": "",
  "blind_sealed": false,
  "blind_seal_receipt_path": null,
  "outcome_access_allowed": false
}
```

`access_log.jsonl`은 최소 다음 logical_role에 대한 모든 핵심 파일 접근을 기록한다.

```text
main_prompt
news_csv
research_daily_manifest
research_daily_schema
research_daily_calendar
access_json
blind_snapshot
blind_artifact
blind_seal_receipt
outcome_snapshot
outcome_artifact
final_bundle
```

각 access record는 다음 필드를 가진다.

```json
{
  "access_id": "ACC-000001",
  "phase": "PHASE_0_ACQUIRE_INPUT",
  "operation": "download|open|stat|read_bytes|sha256|header_read|row_count|parse|print|delete|quarantine|write",
  "logical_role": "news_csv",
  "path_or_url": "",
  "allowed_in_phase": true,
  "content_bytes_accessed": false,
  "timestamp": "",
  "note": ""
}
```

validator는 `access_log.jsonl`을 직접 읽어 다음 값을 계산한다.

```text
preseal_outcome_download_count
preseal_outcome_stat_count
preseal_outcome_read_bytes_count
preseal_outcome_sha256_count
preseal_outcome_header_read_count
preseal_outcome_row_count_count
preseal_outcome_parse_count
preseal_outcome_print_count
preseal_outcome_used_in_blind_graph_count
preseal_outcome_download_count_zero
blind_graph_rebuilt_after_repair
preseal_outcome_content_access_count
preseal_outcome_label_access_count
```

ACCEPT_FULL 조건은 **이번 실행의 유일한 accepted attempt 기준**으로 계산한다. v23에서는 discarded attempt의 preseal outcome byte 접촉을 accepted run으로 세탁하지 않는다. 같은 assistant 실행 안에서 outcome 파일의 byte/stat/hash/header/row_count/parse/label을 PHASE_2 전에 만졌다면 그 실행은 brain import-ready가 아니다.

```text
attempt_history_present == true
accepted_attempt.status == ACCEPTED_CLEAN
access_log_present == true
phase_state_transition_valid == true
blind_seal_receipt_verified_before_outcome_access == true
accepted_attempt.preseal_outcome_download_count == 0
accepted_attempt.preseal_outcome_stat_count == 0
accepted_attempt.preseal_outcome_read_bytes_count == 0
accepted_attempt.preseal_outcome_sha256_count == 0
accepted_attempt.preseal_outcome_header_read_count == 0
accepted_attempt.preseal_outcome_row_count_count == 0
accepted_attempt.preseal_outcome_parse_count == 0
accepted_attempt.preseal_outcome_print_count == 0
accepted_attempt.preseal_outcome_content_access_count == 0
accepted_attempt.preseal_outcome_label_access_count == 0
accepted_attempt.preseal_outcome_used_in_blind_graph_count == 0
any_attempt_preseal_outcome_byte_touch_count == 0
semantic_contamination_seen_in_any_attempt == false
```

아래 패턴은 더 이상 ACCEPT_FULL 금지 사유가 아닌 recoverable로 취급하지 않는다.

```text
preseal outcome download/stat/sha256/byte_size/header/row_count metadata only
AND no data row/sample/parse/price label/winner/output
AND attempt discarded before BLIND graph/final_watchlist generation
AND clean-room attempt rebuilt BLIND from scratch
```

이 패턴이 발생하면 해당 세션의 산출물은 다음으로 잠근다.

```text
bundle_status = QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

`access_log.jsonl`이 없거나 validator가 로그를 실제로 읽지 않으면:

```text
bundle_status = QUARANTINE_MISSING_ACCESS_LOG
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

BLIND report의 “D snapshot 다운로드 0” 선언은 access_log 계산값과 같아야 한다. 둘이 다르면 access_log를 진실로 보고 `QUARANTINE_PHASE_AUDIT_MISMATCH`로 처리한다.

────────────────────────────────────────


────────────────────────────────────────
SEMANTIC TYPE SYSTEM LOCK v18.1 — quote_role ↔ catalyst_type 일반 타입 검사
────────────────────────────────────────

이 섹션은 특정 종목명 예외표가 아니다. 20241213류 오류를 종목명 blacklist로 막지 않고, **quote의 역할과 경제 사건 타입의 호환성**으로 막는다.

핵심 원칙:

```text
문자열이 상장사명과 일치한다 ≠ issuer-scoped fact
기사에 회사명이 등장한다 ≠ final catalyst
primary_quote가 catalyst_type을 직접 지지한다 = final candidate의 최소 조건
```

final_watchlist에 들어가는 모든 후보는 아래 4단계를 모두 통과해야 한다.

```text
1. entity_binding_role이 issuer-scoped 역할인가
2. quote_role이 final 근거로 허용되는 역할인가
3. material_fact_class가 catalyst_type과 호환되는가
4. economic_mechanism이 fact에 의해 SUPPORTED inference로 닫혔는가
```

하나라도 실패하면 해당 후보는 `final_watchlist`에 들어갈 수 없다. 단, 원문 정보는 버리지 않고 `row_disposition`, `entity_ledger_blind`, `candidate_screening`, `brain_delta`에 `REJECTED_SEMANTIC_TYPE_MISMATCH`, `WATCH_SECONDARY`, `NEGATIVE_CONTROL`, `ERROR_CASE` 중 하나로 보존한다.

## SC0. final_watchlist는 정확히 20개가 아니라 최대 20개다

`final_watchlist`는 실제 장전 decision label이다. 강한 후보가 4개면 4개로 끝낸다.

금지:

```text
20개를 채우려고 약한 후보를 final에 올림
P snapshot feature만 남는 후보를 final에 올림
primary_quote가 단순 언급인데 final에 올림
quote_role이 final 금지인데 final에 올림
```

ACCEPT_FULL 조건:

```text
final_watchlist_size <= 20
final_watchlist_no_padding == true
final_filler_low_quality_count == 0
final_quality_tier_A_or_B_count == final_watchlist_size
```

## SC1. Entity binding은 문자열 일치가 아니라 local issuer role이다

`blind_snapshot(P)`에 같은 회사명이 있어도 그것만으로 issuer-scoped observation이 아니다.

각 mention은 반드시 아래 중 하나의 `entity_binding_role`을 가진다.

### final 허용 issuer role

```text
ARTICLE_MAIN_SUBJECT
DIRECT_SECONDARY_ACTOR_WITH_LOCAL_ECONOMIC_ACTION
EXPLICIT_LOCAL_TICKER_CODE_MATCH
ANALYST_TARGET_WITH_NUMERIC_BRIDGE
FORMAL_DISCLOSURE_ISSUER
```

허용 role의 의미:

```text
ARTICLE_MAIN_SUBJECT
= 제목 또는 lead paragraph에서 해당 issuer가 문장의 주어이고, 밝혔다/공시했다/체결했다/선정됐다/출시했다/허가받았다/수주했다/공급한다 등 local predicate를 가진다.

DIRECT_SECONDARY_ACTOR_WITH_LOCAL_ECONOMIC_ACTION
= 기사 주인공은 아니지만 같은 문장 또는 인접 1문장 안에서 해당 issuer에게 직접 귀속되는 계약·공급·허가·제품·자본정책·실적 bridge가 존재한다.

EXPLICIT_LOCAL_TICKER_CODE_MATCH
= 같은 문장·괄호·대괄호·종목코드 label 안에서 회사명과 6자리 ticker가 함께 확인된다.

ANALYST_TARGET_WITH_NUMERIC_BRIDGE
= 리포트 제목·본문에서 해당 issuer가 분석 대상이고, 목표가·실적 전망·매출/영업이익 bridge 등 수치 근거가 해당 issuer에 귀속된다.

FORMAL_DISCLOSURE_ISSUER
= 공시 제목 또는 공시 본문에서 해당 법인이 공시 주체로 명시된다.
```

### final 금지 issuer role

```text
MENTION_ONLY_CONTEXT
HOMONYM_OR_GENERIC_WORD_COLLISION
GROUP_BRAND_OR_AFFILIATE_UNRESOLVED
PERSON_OR_COMMON_NOUN_COLLISION
FOREIGN_OR_NONLISTED_ENTITY
TABLE_ROW_MARKET_STATE_ONLY
MANUFACTURER_ONLY
ATTENDEE_LIST_ONLY
INVESTOR_HOLDING_MENTION_ONLY
PRODUCT_ADJECTIVE_OR_BRAND_ONLY
PLACE_OR_NATURE_PHENOMENON
POLICY_ACRONYM_OR_COMMON_NOUN
PRESENTATION_OR_SEMINAR_ONLY
IR_CALENDAR_ONLY
CSR_OR_ROUTINE_ONLY
THEME_BASKET_MEMBER_ONLY
MARKET_FLOW_TABLE_MEMBER_ONLY
THIRD_PARTY_RETAIL_DISCOUNT_ONLY
```

ACCEPT_FULL 조건:

```text
final_candidate_entity_role_allowed_count == final_watchlist_size
final_candidate_mention_only_count == 0
final_candidate_homonym_collision_count == 0
final_candidate_generic_word_collision_count == 0
final_candidate_forbidden_quote_role_count == 0
postseal_accepted_issuer_false_positive_count == 0
```

## SC2. Ambiguous literal은 종목명 denylist가 아니라 proof-strength gate로 처리한다

특정 이름을 영구 blacklist로 만들지 않는다. 대신 아래 특성을 가진 literal은 `ambiguous_literal=true`로 표시하고, local proof가 없으면 issuer-scoped로 승인하지 않는다.

ambiguous literal 조건:

```text
짧은 한글 1~2음절 상장사명
영문 대문자 일반 단어 또는 약어와 같은 상장사명
정책·금융·규제 약어와 같은 문자열
제품 수식어·브랜드 문구와 같은 문자열
자연현상·장소·방향·상태를 뜻하는 일반명사와 같은 문자열
사람 이름·직함·단체명과 충돌할 수 있는 문자열
제목 안에서 회사명이 아니라 상품명·문장 수식어·표 항목으로 쓰인 문자열
```

ambiguous literal은 다음 중 하나가 같은 row의 local context에 있어야 `ACCEPT_FOR_SCREENING` 가능하다.

```text
명시 6자리 ticker가 해당 literal과 같은 문장·괄호·대괄호 안에 존재
공시 제목의 법인 표기: (주)회사명, 회사명(주), 주식회사 회사명
제목 또는 첫 문단에서 회사명은/가/은 ... 밝혔다/공시했다/체결했다/선정됐다/출시했다/허가받았다
애널리스트 리포트 제목에서 명확한 분석 대상: "회사명, ... 목표가" 또는 "회사명에 대해"
회사 IR/공시/보도자료 형태로 issuer가 발표 주체임이 확인됨
```

없으면:

```text
binding_status = REJECTED_CONTEXTUAL_FALSE_POSITIVE
issuer_scoped = false
candidate_generation_eligible = false
training_eligible = false
```

주의:

```text
ambiguous_literal gate는 종목명 하드코딩이 아니다.
같은 literal이라도 issuer local proof가 있으면 통과하고, 없으면 거절한다.
validator가 특정 literal을 무조건 reject하는 방식으로 구현되면 ACCEPT_FULL 금지다.
```

## SC3. quote_role과 material_fact_class를 분리한다

각 final candidate의 primary fact는 반드시 다음 3개를 가진다.

```text
quote_role
material_fact_class
catalyst_type
```

### final 허용 quote_role

```text
ISSUER_CONTRACT_ACTION
ISSUER_ORDER_OR_SUPPLY_ACTION
ISSUER_PROJECT_AWARDED_ACTION
ISSUER_PRODUCT_RELEASE_ACTION
ISSUER_SERVICE_RELEASE_ACTION
ISSUER_COMMERCIALIZATION_ACTION
ISSUER_REGULATORY_APPROVAL_ACTION
ISSUER_CLINICAL_OR_PIPELINE_STAGE_ACTION
ISSUER_GOVERNMENT_PROJECT_SELECTION_ACTION
ISSUER_LICENSE_OR_TECH_TRANSFER_ACTION
ISSUER_CAPITAL_POLICY_ACTION
ISSUER_STRATEGIC_INVESTMENT_OR_CONTROL_ACTION
ISSUER_ANALYST_NUMERIC_BRIDGE
ISSUER_EXPLICIT_MARKET_STATE_NOTICE
```

### final 금지 quote_role

```text
COMMON_NOUN_ONLY
POLICY_ACRONYM_ONLY
PLACE_OR_NATURE_PHENOMENON_ONLY
PRODUCT_ADJECTIVE_OR_BRAND_ONLY
MANUFACTURER_ONLY
ATTENDEE_LIST_ONLY
INVESTOR_HOLDING_ONLY
MARKET_FLOW_TABLE_MEMBER_ONLY
THEME_LIST_MEMBER_ONLY
IR_CALENDAR_ONLY
PRESENTATION_OR_SEMINAR_ONLY
CSR_OR_ROUTINE_ONLY
TECHNICAL_SIGNAL_ONLY
GENERAL_MARKET_COMMENTARY_ONLY
THIRD_PARTY_RETAIL_DISCOUNT_ONLY
INDEX_COMPONENT_ONLY
AFFILIATE_OR_GROUP_MENTION_UNRESOLVED
```

## SC4. material_fact_class taxonomy

허용 material fact class:

```text
CONTRACT_SIGNED
ORDER_RECEIVED
SUPPLY_AGREEMENT
PROJECT_AWARDED
PRODUCT_LAUNCHED_BY_ISSUER
PRODUCT_COMMERCIALIZATION_BY_ISSUER
SERVICE_RELEASE_BY_ISSUER
LICENSE_OR_TECH_TRANSFER_WITH_RIGHTS
REGULATORY_APPROVAL
CLINICAL_STAGE_ADVANCE
GOVERNMENT_PROJECT_SELECTED
DIVIDEND
BUYBACK
SHARE_CANCELLATION
RIGHTS_ISSUE
THIRD_PARTY_ALLOCATION
MERGER_OR_SPINOFF
STAKE_SALE_OR_CONTROL_CHANGE
ANALYST_NUMERIC_EARNINGS_BRIDGE
EXPLICIT_MARKET_STATE_NOTICE
```

final 금지 fact class:

```text
MERE_ATTENDANCE
MERE_MANUFACTURER_MENTION
MERE_HOLDER_MENTION
MERE_INSTITUTIONAL_FLOW
MERE_TECHNICAL_SIGNAL
MERE_MARKET_COMMENTARY
MERE_EVENT_PROMOTION
MERE_PRODUCT_DISCOUNT_BY_OTHER_RETAILER
MERE_THEME_LIST
MERE_INDEX_COMPONENT
MERE_REGULATORY_ENVIRONMENT_WITHOUT_ISSUER_ACTION
MERE_PRESENTATION_OR_SEMINAR
MERE_IR_CALENDAR
MERE_CSR_OR_ROUTINE
MERE_AFFILIATE_OR_GROUP_CONTEXT
MERE_FOREIGN_OR_NONLISTED_CONTEXT
```

## SC5. catalyst_type compatibility matrix

`catalyst_type`은 `material_fact_class`와 호환되어야 한다. 호환되지 않으면 점수 감점이 아니라 final 금지다.

```text
CONTRACT_ORDER
  허용 fact_class:
  - CONTRACT_SIGNED
  - ORDER_RECEIVED
  - SUPPLY_AGREEMENT
  - PROJECT_AWARDED

PRODUCT_COMMERCIALIZATION
  허용 fact_class:
  - PRODUCT_LAUNCHED_BY_ISSUER
  - PRODUCT_COMMERCIALIZATION_BY_ISSUER
  - SERVICE_RELEASE_BY_ISSUER

BIO_STAGE_ADVANCE
  허용 fact_class:
  - REGULATORY_APPROVAL
  - CLINICAL_STAGE_ADVANCE
  - LICENSE_OR_TECH_TRANSFER_WITH_RIGHTS
  - GOVERNMENT_PROJECT_SELECTED

CAPITAL_POLICY
  허용 fact_class:
  - DIVIDEND
  - BUYBACK
  - SHARE_CANCELLATION
  - RIGHTS_ISSUE
  - THIRD_PARTY_ALLOCATION
  - MERGER_OR_SPINOFF
  - STAKE_SALE_OR_CONTROL_CHANGE

STRATEGIC_INVESTMENT
  허용 fact_class:
  - THIRD_PARTY_ALLOCATION
  - STAKE_SALE_OR_CONTROL_CHANGE
  - MERGER_OR_SPINOFF

ANALYST_BRIDGE
  허용 fact_class:
  - ANALYST_NUMERIC_EARNINGS_BRIDGE

CONTINUATION_EXPLICIT
  허용 fact_class:
  - EXPLICIT_MARKET_STATE_NOTICE

OTHER_CONCRETE
  허용 fact_class:
  - 위 허용 fact_class 중 하나여야 하며, 별도 explanation_required=true
```

ACCEPT_FULL 조건:

```text
final_candidate_material_fact_count >= final_watchlist_size
final_candidate_catalyst_type_matrix_failure_count == 0
final_candidate_material_fact_forbidden_count == 0
```

## SC6. final 금지 quote_role → catalyst_type 예시

아래는 종목별 금지표가 아니라 role-level 금지표다. 어떤 ticker든 같은 원칙을 적용한다.

```text
MERE_ATTENDANCE + BIO_STAGE_ADVANCE = 금지
MERE_MANUFACTURER_MENTION + BIO_STAGE_ADVANCE = 금지
MERE_HOLDER_MENTION + CONTRACT_ORDER = 금지
MERE_INSTITUTIONAL_FLOW + PRODUCT_COMMERCIALIZATION = 금지
PRESENTATION_OR_SEMINAR_ONLY + CONTRACT_ORDER = 금지
IR_CALENDAR_ONLY + CONTRACT_ORDER = 금지
POLICY_ACRONYM_ONLY + PRODUCT_COMMERCIALIZATION = 금지
PLACE_OR_NATURE_PHENOMENON_ONLY + CONTRACT_ORDER = 금지
PRODUCT_ADJECTIVE_OR_BRAND_ONLY + PRODUCT_COMMERCIALIZATION = 금지
THEME_LIST_MEMBER_ONLY + PRODUCT_COMMERCIALIZATION = 금지
```

이 표는 예시이며 폐쇄형 목록이 아니다. quote의 local role이 issuer economic action이 아니면 final 근거가 될 수 없다.

## SC7. economic_mechanism은 SUPPORTED inference로 닫는다

final item의 `economic_mechanism`, `why_now`, `red_team_counterargument`는 자유 템플릿이 아니다.

각 final item은 반드시 다음 필드를 가진다.

```json
{
  "issuer_role_anchor_quote": "",
  "issuer_role_anchor_type": "ARTICLE_MAIN_SUBJECT | DIRECT_SECONDARY_ACTOR_WITH_LOCAL_ECONOMIC_ACTION | EXPLICIT_LOCAL_TICKER_CODE_MATCH | ANALYST_TARGET_WITH_NUMERIC_BRIDGE | FORMAL_DISCLOSURE_ISSUER",
  "issuer_role_anchor_fact_id": "FACT-...",
  "catalyst_entailment_quote": "",
  "catalyst_entailment_fact_id": "FACT-...",
  "quote_role": "",
  "material_fact_class": "",
  "mechanism_inference_id": "INF-...",
  "mechanism_support_type": "SUPPORTED_INFERENCE",
  "mechanism_audit_verdict": "PASS",
  "final_quality_tier": "A_DIRECT | B_STRONG_THEME"
}
```

금지:

```text
final item의 supporting_inference_ids == []
facts > 0 이지만 inferences == 0인 상태에서 final_watchlist > 0
mechanism_inference_id == null
mechanism_audit_verdict != PASS
fact에는 단순 언급만 있는데 수주·공급·허가·상용화 mechanism 생성
```

ACCEPT_FULL 조건:

```text
final_supported_economic_inference_count == final_watchlist_size
final_unsupported_economic_inference_count == 0
final_template_mechanism_without_inference_count == 0
```

## SC8. final_semantic_audit.jsonl 필수

최종 bundle에는 다음 block을 반드시 포함한다.

```text
<!-- NSLAB:BEGIN final_semantic_audit.jsonl -->
...
<!-- NSLAB:END final_semantic_audit.jsonl -->
```

각 final item마다 audit row 하나를 만든다.

필수 필드:

```text
candidate_id
rank
ticker
name
primary_fact_id
primary_quote
entity_binding_role
quote_role
issuer_role_anchor_type
issuer_role_anchor_valid
material_fact_class
catalyst_type
quote_role_catalyst_alignment_valid
catalyst_entailment_valid
mechanism_inference_id
mechanism_supported
final_quality_tier
semantic_verdict = PASS | FAIL
fail_reasons
```

ACCEPT_FULL 조건:

```text
final_semantic_audit_present == true
final_semantic_audit_row_count == final_watchlist_size
final_semantic_audit_fail_count == 0
```

없거나 fail row가 있으면:

```text
bundle_status = QUARANTINE_MISSING_OR_FAILED_FINAL_SEMANTIC_AUDIT
brain_eligible = false
ACCEPT_FULL 금지
```

## SC9. semantic regression fixture는 종목명이 아니라 role을 검사한다

validator는 매 실행마다 synthetic role fixture를 실행한다. fixture는 특정 상장사명을 금지하지 않고, 아래 role들이 어떤 ticker에 붙더라도 final로 승격되지 않는지 검사한다.

필수 fixture role:

```text
PLACE_OR_NATURE_PHENOMENON_ONLY
POLICY_ACRONYM_ONLY
PRODUCT_ADJECTIVE_OR_BRAND_ONLY
MERE_MANUFACTURER_MENTION
MERE_HOLDER_MENTION
ATTENDEE_LIST_ONLY
MARKET_FLOW_TABLE_MEMBER_ONLY
PRESENTATION_OR_SEMINAR_ONLY
IR_CALENDAR_ONLY
THEME_LIST_MEMBER_ONLY
THIRD_PARTY_RETAIL_DISCOUNT_ONLY
```

각 fixture의 기대값:

```text
entity_binding_role is forbidden OR quote_role is forbidden
candidate_generation_eligible == false OR final_eligible == false
training_eligible == false unless it is an error/negative-control record
```

ACCEPT_FULL 조건:

```text
semantic_role_fixture_count >= 11
semantic_role_fixture_failure_count == 0
```

호환성:

```text
기존 validator가 semantic_regression_20241213_* 이름을 기대하면, 그 counter는 종목명 회귀가 아니라 role-class fixture 결과의 alias로만 사용한다.
semantic_regression_20241213_failure_count_zero == semantic_role_fixture_failure_count == 0
```

## SC10. DART/공시 HTML body cleanup

DART 공시형 body에는 CSS, style, color code, form markup, href token이 섞일 수 있다. 이를 그대로 회사·ticker 근거로 쓰지 않는다.

전처리 규칙:

```text
<style>...</style>, CSS selector, BODY/FONT-FAMILY/COLOR/WIDTH, xforms markup 제거
HTML color/link token 000000, 194866, 333333, 666666 등은 ticker 후보에서 제외
임의 6자리 숫자를 ticker로 쓰지 않음
명시 ticker는 회사명과 같은 문장·괄호·대괄호·종목코드 label 안에서만 local ticker로 인정
```

ACCEPT_FULL 조건:

```text
html_css_token_as_ticker_count == 0
article_level_ticker_propagation_count == 0
local_ticker_ownership_reverified == true
```

## SC11. semantic gate summary counters

최종 bundle의 `validation_report.json`, `bundle_manifest.json`, `direct_ingest_contract.json`에는 아래 계산값이 모두 있어야 한다.

```text
final_candidate_count
final_candidate_material_event_pass_count
final_candidate_false_positive_literal_count
final_candidate_mention_only_count
final_candidate_event_role_forbidden_count
final_candidate_quote_role_forbidden_count
final_candidate_catalyst_type_matrix_failure_count
final_candidate_quote_role_catalyst_alignment_failure_count
final_candidate_mechanism_support_failure_count
final_candidate_no_padding_verified
final_semantic_audit_row_count
final_semantic_audit_fail_count
semantic_role_fixture_count
semantic_role_fixture_failure_count
html_css_token_as_ticker_count
article_level_ticker_propagation_count
```

ACCEPT_FULL 조건:

```text
final_candidate_false_positive_literal_count == 0
final_candidate_mention_only_count == 0
final_candidate_event_role_forbidden_count == 0
final_candidate_quote_role_forbidden_count == 0
final_candidate_catalyst_type_matrix_failure_count == 0
final_candidate_quote_role_catalyst_alignment_failure_count == 0
final_candidate_mechanism_support_failure_count == 0
final_candidate_no_padding_verified == true
final_semantic_audit_fail_count == 0
semantic_role_fixture_failure_count == 0
html_css_token_as_ticker_count == 0
article_level_ticker_propagation_count == 0
```

────────────────────────────────────────
────────────────────────────────────────

DIRECT-INGEST GOLD LOCK — 사람 후검수 없는 자동 import-ready 산출물 규칙
────────────────────────────────────────

이 섹션은 이 프롬프트의 최상위 품질 계약이다. 아래 본문·기존 GOLD-RUN HARD GUARD·일반 실행 절차보다 우선한다.

목표는 단순히 사람이 읽기 좋은 연구 보고서가 아니라, repo의 자동 importer가 사람 후검수 없이 바로 수입할 수 있는 `ingest-ready research bundle`을 생성하는 것이다.

정확한 의미는 다음과 같다.

```text
GPT 세션의 책임:
- CSV와 research_daily를 실제 확보·파싱·검증한다.
- BLIND 패킷을 물리적으로 먼저 봉인한다.
- outcome을 post-seal 이후에만 연다.
- canonical_graph에서 모든 artifact를 렌더링한다.
- 실행 코드 validator로 전수 검증한다.
- 최종 Markdown 자체가 자동 import에 필요한 모든 record·hash·count·contract를 포함하게 한다.

repo 자동 로딩 공정의 책임:
- 생성된 Markdown을 import한다.
- warehouse/brain을 rebuild한다.

금지:
- 사람이 나중에 읽고 고쳐야 하는 ACCEPT_FULL
- repo import 단계에서 오류가 잡힐 것을 기대한 ACCEPT_FULL
- “대체로 좋아 보임”을 ACCEPT_FULL로 쓰는 것
```

## D0. Direct ingest-ready의 판정

최종 bundle이 `ACCEPT_FULL` 및 `brain_eligible=true`를 선언하려면 다음도 반드시 true여야 한다.

```text
direct_brain_ingest_ready == true
requires_manual_research_review == false
requires_posthoc_prompt_repair == false
requires_human_semantic_review == false
automated_import_expected_to_pass == true
```

이 값들은 LLM 자기 선언이 아니라 `validate_nslab_bundle.py`가 계산한 hard gate 결과에서만 온다.

다음 중 하나라도 발생하면 `direct_brain_ingest_ready=false`이며 `ACCEPT_FULL` 금지다.

```text
schema_version != nslab.research_bundle.v11
artifact_type != research_episode_bundle
final_watchlist_size > 20
Markdown final watchlist table이 20개 초과
final_watchlist rank가 1..N 연속이 아님
candidate pool이 final_watchlist에 섞임
final item에 primary_fact_ids가 없음
final item의 thesis가 일반 템플릿·시장 feature만으로 구성됨
final item의 economic_mechanism이 없음
final item의 economic_mechanism이 supporting_inference_ids 또는 mechanism_fact_ids로 검증되지 않음
final item의 primary_quote가 catalyst_type을 직접 지지하지 않음
final item이 contextual false positive entity mention에 기반함
final item의 red_team_counterargument가 후보 고유 실패조건이 아님
brain_delta가 record-level population이 아니라 요약 교훈 중심임
brain_delta_count_by_type와 issuer_day/direct_event/theme/pair count가 불일치
renderer와 validator가 독립 검증 책임을 갖지 않음
renderer_sha256 == validator_sha256인데 별도 entrypoint hash가 없음
validator의 expected_source가 GENERATED_OUTPUT 또는 SELF_DECLARED_MANIFEST임
outcome snapshot이 BLIND seal 전에 다운로드·해시·parse·출력됨
access_log.jsonl이 없거나 validator가 access_log를 실제로 읽지 않음
phase_state transition이 순서대로 기록되지 않음
BLIND report의 D snapshot 다운로드 0 선언과 access_log 계산값이 모순됨
BLIND report에 D outcome 숫자·response·상한가 적중이 섞임
source/fact/id orphan이 하나라도 있음
fact exact_quote가 원문 row에 없거나 offset이 틀림
semantic audit이 NOT_ENTAILED/UNSUPPORTED/cross-event leak를 남김
bundle_manifest count/hash와 실제 artifact가 다름
validation_report의 critical_error_count > 0
validator_exit_code != 0
direct_ingest_contract.json block이 누락됨
final_semantic_audit.jsonl block이 누락됨
final_semantic_audit_fail_count > 0
direct_ingest_contract.record_import_manifest count가 실제 brain_delta/source/fact/id count와 불일치
direct_ingest_contract.hard_gate_summary가 validation_report와 모순됨
repair_loop가 필요한 오류를 발견했는데 자동 수리·재검증을 수행하지 않음
final candidate의 issuer_role_anchor_type 또는 quote_role이 SC1/SC3 final 허용 role 집합에 속하지 않음
final candidate가 mention-only, homonym collision, generic word collision, attendee list, market flow table, manufacturer-only, investor-holding-only, policy/common-noun, product-adjective role에서 발생함
final candidate의 catalyst_type이 primary_quote/supporting_fact trigger와 불일치
final candidate의 economic_mechanism이 supported inference가 아니라 템플릿 문구임
final candidate가 P snapshot/시총/회전율/최근고가만으로 승격됨
validator boolean check가 actual=0 expected=0 또는 문자열 present만으로 통과됨
renderer_sha256 == validator_sha256인데 combined entrypoint 예외 증거가 없음
why_now/economic_mechanism/red_team 문장이 중간에서 끊김
```

위 조건 중 하나라도 true인데 `bundle_status: ACCEPT_FULL` 또는 `brain_eligible: true`를 쓰는 것은 오류다.

## D1. `direct_ingest_contract.json` artifact 필수

최종 bundle에는 다음 block을 반드시 포함한다.

```text
<!-- NSLAB:BEGIN direct_ingest_contract.json -->
...
<!-- NSLAB:END direct_ingest_contract.json -->
```

스키마는 최소 다음을 포함한다.

```json
{
  "schema_version": "nslab.direct_ingest_contract.v1",
  "episode_id": "",
  "trade_date": "",
  "direct_brain_ingest_ready": true,
  "requires_manual_research_review": false,
  "requires_posthoc_prompt_repair": false,
  "requires_human_semantic_review": false,
  "automated_import_expected_to_pass": true,
  "expected_import_command": "nslab research import-bundle <bundle_path> --validate --accept",
  "expected_followup_commands": [
    "nslab warehouse rebuild",
    "nslab brain rebuild --mode llm-full"
  ],
  "hard_gate_summary": {
    "schema_contract_verified": true,
    "csv_full_parse_complete": true,
    "research_daily_access_verified": true,
    "blind_packet_sealed": true,
    "outcome_access_after_seal_only": true,
    "access_log_present": true,
    "phase_state_transition_valid": true,
    "preseal_outcome_touch_zero": true,
    "blind_report_access_log_parity_verified": true,
    "final_watchlist_size_lte_20": true,
    "markdown_final_watchlist_size_lte_20": true,
    "renderer_validator_independence_verified": true,
    "brain_delta_record_level": true,
    "brain_delta_record_type_canonical_verified": true,
    "candidate_ranking_audit_schema_verified": true,
    "candidate_ranking_audit_rankable_coverage_verified": true,
    "record_count_hash_parity_ready": true,
    "direct_ingest_contract_count_hash_mirror_verified": true,
    "source_fact_id_closure_verified": true,
    "critical_error_count": 0,
    "validator_exit_code": 0
  },
  "record_import_manifest": {
    "brain_delta_record_count": 0,
    "training_eligible_record_count": 0,
    "candidate_ranking_audit_count": 0,
    "candidate_screening_include_or_watch_count": 0,
    "issuer_day_case_count": 0,
    "direct_event_case_count": 0,
    "theme_formation_case_count": 0,
    "blind_leader_pair_count": 0,
    "source_ledger_count": 0,
    "fact_ledger_count": 0,
    "id_registry_count": 0,
    "access_log_count": 0,
    "final_semantic_audit_count": 0,
    "brain_delta_jsonl_sha256": "",
    "candidate_ranking_audit_jsonl_sha256": "",
    "validation_report_sha256": ""
  },
  "fatal_blockers": []
}
```

`direct_brain_ingest_ready=true`인 경우 `fatal_blockers`는 반드시 빈 배열이어야 한다. 하나라도 blocker가 있으면 상태는 다음으로 둔다.

```text
bundle_status = QUARANTINE_DIRECT_INGEST_NOT_READY
brain_eligible = false
direct_brain_ingest_ready = false
```

최종 파일의 `direct_ingest_contract.json`은 중간 작업 영수증이 아니다. 다음 문자열은 최종 `ACCEPT_FULL` bundle 안에 남기지 않는다.

```text
READY_FOR_FINAL_REPARSE
PENDING_FINAL_REPARSE
READY_FOR_SCHEMA_FIX
```

위 상태가 필요했다면 repair loop 중간 artifact에만 남기고, 최종 Markdown은 repair 이후의 ready=true contract로 다시 렌더한다.

## D2. 0622 fixture parity contract

`20260622_nslab_episode_bundle.example.md`는 숫자값이 아니라 구조 fixture다.

최종 bundle은 0622와 최소한 다음 구조가 같아야 한다.

```text
front matter:
- schema_version: nslab.research_bundle.v11
- artifact_type: research_episode_bundle
- bundle_status: ACCEPT_FULL 또는 명시적 QUARANTINE
- blind_valid
- blind_packet_manifest_sha256
- sealed_blind_report_sha256
- research_daily_access_sha256
- blind_snapshot_sha256
- outcome_snapshot_sha256
- canonical_graph_sha256
- renderer_version / renderer_sha256
- validator_version / validator_sha256
- validator_exit_code

logical blocks:
- research_report.md
- blind_report.md
- postmortem_report.md
- blind_prediction.json
- row_disposition.jsonl
- entity_ledger_blind.jsonl
- fact_ledger_blind.jsonl
- inference_ledger_blind.jsonl
- candidate_screening.jsonl
- blind_packet_manifest.json
- entity_resolution.jsonl
- outcome_ledger.jsonl
- research_episode.json
- brain_delta.jsonl
- source_ledger.jsonl
- id_registry.json
- validation_report.json
- bundle_manifest.json
- direct_ingest_contract.json
- final_semantic_audit.jsonl
- market_state_override_audit.jsonl
- body_table_candidate_generation_audit.jsonl
- ledger_population_audit.json
- outcome_leader_census.jsonl
- outcome_to_news_audit.jsonl
- phase_state.json
- access_log.jsonl
- phase_audit_report.json
```

추가 artifact는 허용되지만, 위 필수 artifact가 하나라도 없으면 `ACCEPT_FULL` 금지다.

## D3. Renderer/validator 독립성은 최상위 fatal gate

`renderer_sha256 == validator_sha256`이면 기본적으로 fatal이다.

예외는 하나뿐이다. 한 파일에 renderer와 validator가 함께 들어 있는 combined script를 사용한 경우, 다음 세 값을 모두 기록하고 validator가 통과해야 한다.

```text
combined_tool_sha256
renderer_entrypoint_sha256
validator_entrypoint_sha256
```

그리고 반드시 다음이 성립해야 한다.

```text
renderer_entrypoint_sha256 != validator_entrypoint_sha256
validator_does_not_import_renderer_state == true
validator_expected_source_not_generated_output == true
validator_recomputes_from_final_bundle_and_internal_artifacts == true
```

예외 조건을 충족하지 못하면 다음으로 처리한다.

```text
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATOR_NOT_INDEPENDENT
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## D4. Final watchlist는 실제 장전 decision label이다

`final_watchlist`는 후보 저장소가 아니다. 자동 import 시 모델이 “실제 장전 선택”으로 학습하는 label이다.

따라서 각 final item은 반드시 아래 필드를 가진다.

```json
{
  "candidate_id": "CAND-...",
  "rank": 1,
  "ticker": "000000",
  "name": "회사명",
  "catalyst_type": "CONTRACT_ORDER | PRODUCT_COMMERCIALIZATION | BIO_STAGE_ADVANCE | CAPITAL_POLICY | STRATEGIC_INVESTMENT | NAMED_BENEFICIARY | CONTINUATION_EXPLICIT | OTHER_CONCRETE",
  "primary_fact_ids": ["FACT-..."],
  "primary_quote": "원문 최소 인용",
  "source_ids": ["SRC-..."],
  "supporting_inference_ids": [],
  "economic_mechanism": "매출·비용·마진·자본정책·허가확률·수급 중 무엇이 왜 바뀌는지",
  "why_now": "cutoff 이전 새 정보와 safe D-1 feature가 어떻게 결합되는지",
  "red_team_counterargument": "해당 후보 고유 실패조건",
  "outcome_fields_used": false
}
```

다음 문구가 final item의 핵심 논지로 반복되면 fatal이다.

```text
issuer-specific 사건을 직접 FACT 범위에서만 관찰한다
좋은 회사뉴스가 상한가형 사건과 동일하지 않을 수 있다
장전 입력 뉴스의 직접 촉매와 직전 거래일 안전 스냅샷의 거래대금·회전율·탄력 조건을 함께 본 후보다
P시점 소형 시총
P시점 중소형 시총
P시점 거래 회전 존재
최근 5거래일 10% 이상 고가 이력
최근 급등 흔적을 연속성과 선반영 양쪽으로 본다
```

이 문구들은 screening·red-team 보조 설명에는 쓸 수 있으나 final 선정의 중심 이유가 될 수 없다.

validator는 final item마다 다음을 검사한다.

```text
primary_fact_ids_count >= 1
primary_quote_found_in_fact_source == true
economic_mechanism_nonempty == true
why_now_nonempty == true
red_team_counterargument_candidate_specific == true
generic_text_signature_reuse_lte_3 == true
```


## D4.1 score와 rank 의미 고정

`blind_score`, `score`, `feature_score`를 출력할 경우 rank와의 관계를 반드시 선언한다.

허용되는 방식은 둘 중 하나다.

```text
rank_sort_key = blind_score_desc
→ final_watchlist는 score 내림차순이어야 한다.
```

또는

```text
rank_sort_key = committee_rank
→ score는 보조 feature일 뿐이다.
→ 각 final item의 rank_reason에 score보다 우선순위가 달라진 이유를 기록한다.
```

관계 선언 없이 score가 낮은 후보가 rank 1이고 score가 높은 후보가 하위 rank에 있으면 다음으로 처리한다.

```text
rank_score_semantics_verified = false
critical_error_count += 1
bundle_status = QUARANTINE_RANK_SCORE_SEMANTICS
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```

## D5. Brain Delta import parity

`brain_delta.jsonl`은 자동 import 대상이다. 다음 레코드 타입은 count와 payload hash를 manifest에 기록한다.

```text
supervised_issuer_day_case
supervised_direct_event_case
supervised_theme_formation_case
blind_leader_preference_pair
candidate_generation_error_case
ranking_error_case
timing_impossible_case
newsless_or_unexplained_case
negative_control_case
```

각 brain_delta record는 최소 다음을 가진다.

```text
brain_delta_id
record_type
trade_date
available_from
training_eligible
sample_weight
source_fact_ids
source_inference_ids
related_candidate_ids
related_event_ids
related_tickers
outcome_label 또는 outcome_status
eligibility_reason
raw_payload_sha256
```

`training_eligible=true` record에서 다음은 금지한다.

```text
source_fact_ids == [] 이고 source_inference_ids == []
retrospective outcome-only relation을 수혜관계로 승격
candidate_generation_error를 성공 패턴으로 승격
same ticker 여러 event의 sample_weight 합계 > 1
```

`bundle_manifest.json`와 `direct_ingest_contract.json`의 record count는 실제 `brain_delta.jsonl` 파싱 count와 일치해야 한다.

## D6. 실패 처리 원칙

목표는 항상 ingest-ready `ACCEPT_FULL`이다. 하지만 검증되지 않은 `ACCEPT_FULL`은 가장 나쁜 산출물이다.

따라서 다음 우선순위를 따른다.

```text
1. ACCEPT_FULL + direct_brain_ingest_ready=true
2. QUARANTINE_DIRECT_INGEST_NOT_READY + fatal_blockers 명시
3. ACQUIRE_FAILED 또는 DEFERRED_NON_TRADING_DAY
4. 검증되지 않은 ACCEPT_FULL 금지
```

작업자가 도구 제한으로 validator 코드를 실행하지 못하면 다음으로 처리한다.

```text
bundle_status = QUARANTINE_VALIDATOR_NOT_EXECUTED
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```


## D6.1 ACCEPT_FULL target repair loop

이번 실행의 목표 상태는 다음이다.

```text
bundle_status = ACCEPT_FULL
brain_eligible = true
direct_brain_ingest_ready = true
requires_manual_research_review = false
requires_posthoc_prompt_repair = false
automated_import_expected_to_pass = true
```

단, `ACCEPT_FULL`은 선언 목표가 아니라 `validate_nslab_bundle.py`가 계산한 결과다.

validator가 critical error를 발견하면 critical gate를 낮추지 말고 다음 순서로 자동 수리한다.

```text
1. renderer/validator hash 또는 entrypoint 독립성 오류
   - render_nslab_bundle.py와 validate_nslab_bundle.py를 물리적으로 분리한다.
   - 두 파일의 SHA-256을 다시 계산한다.
   - renderer_sha256 != validator_sha256을 만족시킨다.
   - combined script 환경이면 combined_tool_sha256, renderer_entrypoint_sha256, validator_entrypoint_sha256을 모두 기록하고 entrypoint hash가 서로 달라야 한다.

2. final_watchlist가 20개 초과
   - rank 21 이후 항목은 final_watchlist에서 제거한다.
   - 제거한 항목은 candidate_pool, continuation_pool, WATCH_SECONDARY, ranking_error_case 중 하나로 보존한다.
   - Markdown final table과 blind_prediction.json은 canonical_graph에서 다시 렌더링한다.

3. final candidate reason이 약하거나 semantic gate를 위반함
   - P시점 소형시총, 거래회전, 최근 고가 이력, 전일 급등만 근거인 항목은 final에서 내린다.
   - contextual false positive는 quote_role·issuer_role_anchor·material_fact_class 기준으로 issuer_scoped=false로 내리고 final에서 제거한다. 특정 종목명 blacklist로 처리하지 않는다.
   - primary_quote가 catalyst_type을 직접 entail하지 않으면 final에서 제거한다.
   - economic_mechanism이 supporting_inference_ids 또는 mechanism_fact_ids로 검증되지 않으면 final에서 제거하거나 inference를 새로 만들고 독립 검증한다.
   - concrete catalyst가 있고 source fact로 결속된 다음 후보가 있으면 승격한다.
   - 승격할 후보가 없으면 final_watchlist는 20개 미만으로 둔다.
   - final_watchlist rank는 반드시 1..N으로 재번호화한다.

4. final candidate에 source fact 또는 primary quote가 없음
   - 원문 exact_quote가 있는 fact를 연결한다.
   - 연결할 수 없으면 final에서 제거하고 candidate_generation_error 또는 WATCH_SECONDARY로 보존한다.

5. score와 rank 의미 불명확
   - rank_sort_key를 blind_score_desc 또는 committee_rank 중 하나로 선언한다.
   - committee_rank이면 rank_reason에 score보다 우선한 이유를 후보별로 기록한다.

6. brain_delta가 요약문으로 축소됨
   - supervised_issuer_day_case, supervised_direct_event_case, supervised_theme_formation_case, blind_leader_preference_pair, candidate_generation_error_case를 record-level로 재생성한다.
   - record count와 raw_payload_sha256을 bundle_manifest와 direct_ingest_contract에 다시 기록한다.

7. Markdown·JSON·JSONL count/hash 불일치
   - Markdown을 직접 고치지 않는다.
   - canonical_graph를 수정한 뒤 renderer로 전체 artifact를 재렌더링한다.

8. validator가 generated output을 expected로 사용
   - 해당 validator를 폐기하고 다시 작성한다.
   - expected_source는 PROMPT_CONSTANT, INPUT_PARSE, ACCESS_JSON, CANONICAL_GRAPH_PRESEAL, GOLD_SHAPE_CONSTANT 중 하나여야 한다.

9. direct_ingest_contract 불일치
   - validation_report와 bundle_manifest의 실제 계산값을 기준으로 contract를 다시 생성한다.
   - fatal_blockers가 하나라도 남으면 direct_brain_ingest_ready=false로 둔다.

10. BLIND seal 전 outcome 파일 접근 또는 context contamination
   - 같은 실행에서 ACCEPT_FULL로 수리하지 않는다.
   - bundle_status = QUARANTINE_PHASE_CONTAMINATED 또는 QUARANTINE_CONTEXT_CONTAMINATED
   - blind_valid = false
   - brain_eligible = false
   - direct_brain_ingest_ready = false

11. semantic firewall 오류
   - mention-only, homonym, generic word collision, catalyst_type mismatch 후보를 final에서 제거한다.
   - 제거한 후보는 candidate_screening에 EXCLUDE_SEMANTIC_FIREWALL 또는 WATCH_SECONDARY로 보존한다.
   - concrete catalyst가 있고 entity-role/catalyst/inference semantic gates를 모두 통과한 다음 후보만 승격한다.
   - 승격 후보가 부족하면 final_watchlist는 20개 미만으로 둔다.
   - final 후보마다 supported inference를 다시 생성하고 독립 semantic audit을 통과시킨다.
   - role-class fixture 전체와 실제 final 후보 semantic check를 재실행한다.
```

수리 후에는 반드시 아래 순서를 반복한다.

```text
canonical_graph 갱신
→ renderer 재실행
→ validator 재실행
→ validation_report 갱신
→ bundle_manifest 갱신
→ direct_ingest_contract 갱신
→ final bundle 재조립
→ final bundle 재검증
```

repair loop는 최대 16회까지 수행한다.

```text
16회 안에 모든 critical check가 통과하면:
  bundle_status = ACCEPT_FULL
  brain_eligible = true
  direct_brain_ingest_ready = true

16회 후에도 critical error가 남으면:
  bundle_status = QUARANTINE_REPAIR_EXHAUSTED
  brain_eligible = false
  direct_brain_ingest_ready = false
```

절대 금지:

```text
critical check를 warning으로 낮춰서 ACCEPT_FULL 만들기
validator_exit_code를 수동으로 0으로 쓰기
critical_error_count를 수동으로 0으로 쓰기
renderer/validator hash 동일 문제를 무시하기
final_watchlist 20개 초과를 “연속 rank라서 정상”으로 처리하기
weak final reason을 그대로 둔 채 ACCEPT_FULL 선언하기
direct_ingest_contract의 fatal_blockers를 지워서 ready=true로 만들기
```

전체 bundle을 QUARANTINE해야 하는 오류와 개별 record만 training_eligible=false로 내려도 되는 오류를 구분한다.

```text
전체 ACCEPT_FULL 금지:
- schema/front matter 오류
- renderer/validator 독립성 오류
- BLIND 전 outcome 접근
- final_watchlist 20개 초과
- Markdown/JSON final watchlist 불일치
- ID/source/fact orphan
- fact quote 미존재 또는 offset 불일치
- validator self-reference
- brain_delta record population 부족
- issuer_day/direct_event sample_weight group sum 불일치

개별 record downgrade 가능:
- 특정 후보의 인과가 약함
- 테마 수혜주 cutoff 전 결속 부족
- 좋은 뉴스이나 가격 반응 약함
- winner가 newsless/outcome-only임
```
## D7. 최종 응답 원칙

최종 산출물은 실제 다운로드 가능한 Markdown 파일 하나다.

채팅 응답 형식은 사용자의 지시를 따른다. 파일 내부에서는 반드시 `direct_ingest_contract.json`으로 자동 import 가능 여부를 판정한다.


────────────────────────────────────────
BOOTSTRAP ACQUISITION RULE — MAIN PROMPT·CSV 확보 전용
────────────────────────────────────────

ChatGPT 세션에서는 Python/bash/container 네트워크가 GitHub DNS를 풀지 못할 수 있다.

다음 오류는 파일 부재가 아니다.

```text
curl: Could not resolve host
urllib.request.URLError Temporary failure in name resolution
socket.gaierror
NETWORK=caas_packages_only
```

이 오류만으로 MAIN EXECUTION PROMPT, `news_YYYYMMDD.csv`, `research_daily` access/snapshot 확보 실패를 선언하지 않는다.

반드시 같은 YYYYMMDD 파일에 대해 다음 순서로 재시도한다.

```text
1. web/browser 계층으로 GitHub blob 또는 raw page를 연다.
2. 파일 존재·파일명·Raw/View raw 경로를 확인한다.
3. download tool 또는 파일 분석 도구로 로컬에 저장한다.
4. 로컬 저장 후 sha256·byte_size·columns·row_count·min/max published_at·control char count를 검증한다.
5. shell/curl/urllib/requests 실패 이력은 acquisition_warnings에만 남긴다.
6. 모든 web/download fallback까지 실패한 경우에만 ACQUIRE_FAILED를 선언한다.
```

GitHub blob page는 파일 존재·크기·Raw/View raw 경로 확인용으로만 사용한다. blob HTML을 CSV 본문으로 파싱하거나 연구 입력으로 사용하지 않는다.

이미 `/mnt/data`에 같은 이름 파일이 있어도 이번 실행에서 확보한 파일인지 `sha256`, `byte_size`, `row_count`로 재검증한다. 다른 날짜 파일, 샌드박스 잔존 파일, 최신 CSV로 대체하지 않는다.



────────────────────────────────────────
GOLD-RUN HARD GUARD — 20260622 품질 회귀 방지 규칙
────────────────────────────────────────

이 파일 전체가 실행 프롬프트다. 사용자는 별도 설명 대신 이 Markdown을 읽게 할 수 있으며, 이 섹션은 아래 모든 본문보다 우선한다.

이번 실행의 목표는 `20260622_nslab_episode_bundle.example.md`와 같은 gold-shape episode를 만드는 것이다.
단, 20260622의 숫자값을 하드코딩하지 않는다. 20260622는 형식·검증·record 밀도·phase 분리의 기준 fixture이며, 이번 날짜의 실제 `row_count`, `event_count`, `outcome_count`, `brain_delta_record_count`는 입력 데이터에서 계산한다.

## G0. 이번 보강의 재발 방지 대상

다음 사고가 하나라도 발생하면 `ACCEPT_FULL`을 선언하지 않는다.

```text
final_watchlist가 20개를 초과했는데 validator가 통과
validator가 생성된 결과값을 expected로 재사용
candidate pool과 final watchlist 혼합
reason이 P시점 소형시총·거래회전·최근 고가 이력뿐인데 final watchlist 편입
brain_delta를 record-level 학습재료가 아니라 20~40개 요약 교훈으로 축소
schema_version 또는 artifact_type을 gold-shape와 다르게 생성
canonical_graph_sha256·renderer_version·validator_version 누락
context에 이미 D outcome이 있는데 새 BLIND로 ACCEPT_FULL 생성
CSV 확보 실패 후 다른 날짜 CSV 또는 샌드박스 잔존 파일 사용
```

## G1. 자기 결과를 정답으로 삼는 validator 금지

`validate_nslab_bundle.py`는 생성된 결과값을 expected로 사용하면 안 된다.

금지 예:

```text
final_watchlist가 25개라서 expected_rank_set = 1..25로 설정
실제 brain_delta가 42개라서 expected_brain_delta_count = 42로 설정
생성된 marker 개수를 expected marker count로 설정
생성된 schema_version을 expected schema_version으로 설정
생성된 bundle_status를 expected bundle_status로 설정
```

validator의 expected 값은 반드시 다음 중 하나에서만 온다.

```text
1. 이 프롬프트의 명시 상수
2. CSV/price snapshot을 실제 파싱해 계산한 입력 row_count/outcome row_count
3. canonical_graph에서 BLIND seal 전에 확정한 object count
4. research_daily access JSON의 sha256/row_count
5. 20260622 gold fixture에서 가져온 구조적 상수
```

각 critical check는 다음 필드를 반드시 가진다.

```json
{
  "check_id": "...",
  "passed": true,
  "actual": "실제 계산값",
  "expected": "프로토콜 상수 또는 입력에서 계산한 값",
  "expected_source": "PROMPT_CONSTANT | INPUT_PARSE | ACCESS_JSON | CANONICAL_GRAPH_PRESEAL | GOLD_SHAPE_CONSTANT",
  "severity": "critical | warning",
  "error_ids": []
}
```

다음 중 하나라도 있으면 즉시 실패다.

```text
expected_source == GENERATED_OUTPUT
expected_source == SELF_DECLARED_MANIFEST
critical check의 actual 또는 expected가 null
필수 critical check 누락
validator 내부에서 final_watchlist max_rank를 expected rank max로 사용
```

실패 처리:

```text
validator_exit_code = 2
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATOR_SELF_REFERENCE
brain_eligible = false
ACCEPT_FULL 금지
```

## G2. Gold-shape front matter hard contract

최종 bundle front matter는 반드시 다음 값을 사용한다.

```yaml
schema_version: nslab.research_bundle.v11
artifact_type: research_episode_bundle
execution_protocol_version: nslab.brain_grade_semantic_provenance_locked.v11
```

다음 필드는 front matter와 `bundle_manifest.json`에 모두 존재해야 한다.

```text
episode_id
trade_date
previous_trade_date
next_trade_date
window_start
cutoff_at
input_file
input_sha256
blind_packet_manifest_sha256
sealed_blind_report_sha256
research_daily_access_sha256
blind_snapshot_sha256
outcome_snapshot_sha256
canonical_graph_sha256
canonical_graph_object_counts
renderer_version
renderer_sha256
validator_version
validator_sha256
validator_exit_code
created_at
```

금지:

```text
파일이 YAML front matter가 아니라 H1 제목으로 시작함
schema_version: nslab.episode_bundle.v11
schema_version: nslab.episode_bundle.v1
research_episode.json.schema_version: nslab.research_episode.v1
bundle_manifest.json.schema_version: nslab.bundle_manifest.v1
artifact_type: nslab_episode_bundle
canonical_graph_sha256 누락
renderer_version 누락
validator_version 누락
episode_id 누락
direct_brain_ingest_ready 누락
automated_import_expected_to_pass 누락
requires_human_semantic_review 누락
```

위반 시:

```text
bundle_status = QUARANTINE_SCHEMA_CONTRACT_VIOLATION
brain_eligible = false
ACCEPT_FULL 금지
```

## G3. final_watchlist / candidate pool / continuation pool 분리

아래 네 집합을 절대 섞지 않는다.

```text
1. direct_observation_population
   - 전수 관측 모집단
   - 개수 제한 없음

2. candidate_screening_population
   - 모든 observation의 INCLUDE / WATCH / EXCLUDE 심사
   - 개수 제한 없음

3. continuation_watchlist 또는 continuation_pool
   - D-1 수급·고가·회전율 기반 별도 pool
   - final_watchlist가 아님
   - 개수 제한 없음 또는 별도 top N 가능
   - final rank와 다른 continuation_rank를 사용

4. final_watchlist
   - 최종 장전 관심종목
   - 반드시 0~20개
   - 후보가 1개 이상이면 rank는 1부터 N까지 연속
   - N <= 20
   - 통과 후보가 부족하면 20개를 채우지 않는다
```

validator hard check:

```python
final_watchlist_size = len(blind_prediction["final_watchlist"])
rank_set = sorted(item["rank"] for item in blind_prediction["final_watchlist"])
expected_rank_set = list(range(1, final_watchlist_size + 1))

assert final_watchlist_size <= 20
assert rank_set == expected_rank_set
assert max(rank_set, default=0) <= 20
assert duplicate_ticker_count(final_watchlist) == 0
```

다음 방식은 금지한다.

```text
expected_rank_set = list(range(1, max(rank_set)+1))만으로 통과 처리
final_watchlist가 25개인데 rank_sequence_valid=true 처리
rank 21~25를 final_watchlist에 넣고 “연속이라서 유효”로 처리
```

위반 시:

```text
watchlist_size_violation_count += 1
watchlist_rank_over_20_count += 1
critical_error_count += 1
bundle_status = QUARANTINE_FINAL_WATCHLIST_CONTRACT
brain_eligible = false
ACCEPT_FULL 금지
```

## G4. final candidate reason 품질 제한

`final_watchlist`에 들어가는 각 candidate는 다음 중 하나 이상의 concrete catalyst를 가져야 한다.

```text
계약/수주/공급/프로젝트 확정
제품 개발 완료 + 상업화/출시/생산/매출 연결 문구
기술수출/임상/허가/승인/과제 선정 등 바이오 단계 진전
자사주 소각/배당/명확한 주주환원
최대주주/전략투자자 자금 투입 + overhang 완화 근거
cutoff 이전 issuer-scoped event와 직접 연결된 named beneficiary path
```

다음 reason만으로 final_watchlist에 넣으면 안 된다.

```text
P시점 소형 시총
P시점 중소형 시총
P시점 거래 회전 존재
최근 5거래일 10% 이상 고가 이력
최근 5거래일 고가 반복
전일 급등
D-1 상한가
대형주라 탄성 제한
issuer-specific 사건이 있다
일반 테마 문맥이 있다
```

위 feature는 보조 feature로만 허용한다.

각 final item은 반드시 다음을 가진다.

```text
source_fact_ids >= 1
supporting_fact_ids >= 1 또는 supporting_inference_ids >= 1
source_ids >= 1
preopen_thesis가 완전한 문장
why_now가 원문 fact 또는 safe D-1 feature와 연결
red_team_counterargument가 1개 이상
```

`source_event_ids`만 있고 `source_fact_ids`가 없으면 final candidate로 인정하지 않는다.

위반 시:

```text
final_candidate_without_fact_count += 1
weak_final_reason_count += 1
critical_error_count += 1
ACCEPT_FULL 금지
```

## G5. Brain Delta는 요약문이 아니라 record-level 학습 재료다

`brain_delta.jsonl`은 고수준 교훈 20~40개짜리 요약 파일이 아니다.
`news-scalping-lab`이 수입할 record-level memory source다.

반드시 다음 record population을 보존한다.

```text
1. supervised_issuer_day_case
   - trade_date + ticker 단위
   - unique issuer-day 1개당 1 record
   - D outcome label 포함
   - sample_weight 포함
   - training_eligible 명시

2. supervised_direct_event_case
   - screening/event 단위
   - blind decision + source facts + D outcome 포함
   - event-level sample_weight 포함

3. supervised_theme_formation_case
   - sealed theme universe 기준
   - post-seal member mutation 금지

4. blind_leader_preference_pair
   - BLIND에서 봉인한 pair만
   - outcome 뒤 방향성 확인/교정

5. candidate_generation_error_case / ranking_error_case / timing_impossible_case / newsless_case
   - 단, outcome-only 관계를 training_eligible로 승격 금지
```

minimum count contract:

```text
expected_brain_delta_min = max(
    100,
    issuer_day_case_count
  + supervised_direct_event_case_count
  + theme_formation_case_count
  + blind_leader_pair_count
)
```

validator hard check:

```python
assert brain_delta_record_count >= expected_brain_delta_min
assert expected_brain_delta_min >= 100
assert brain_delta_count_by_type["supervised_issuer_day_case"] == issuer_day_case_count
assert brain_delta_count_by_type["supervised_direct_event_case"] == supervised_direct_event_case_count
assert brain_delta_count_by_type["blind_leader_preference_pair"] == blind_leader_pair_count
```

특정 population이 0이면 `validation_report.json`에 그 이유를 기록한다.
`요약 교훈만 생성`은 `ACCEPT_FULL` 사유가 될 수 없다.

위반 시:

```text
brain_delta_underfilled_count += 1
critical_error_count += 1
bundle_status = QUARANTINE_BRAIN_DELTA_UNDERFILLED
brain_eligible = false
ACCEPT_FULL 금지
```

## G6. Canonical graph first

JSON, JSONL, Markdown 표를 따로 만들지 않는다.

반드시 먼저 내부 작업 디렉터리에 `canonical_graph.json`을 만든다.
그 뒤 renderer는 canonical graph에서만 다음을 렌더링한다.

```text
blind_report.md
postmortem_report.md
research_report.md
blind_prediction.json
row_disposition.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
final_semantic_audit.jsonl
market_state_override_audit.jsonl
body_table_candidate_generation_audit.jsonl
ledger_population_audit.json
outcome_leader_census.jsonl
outcome_to_news_audit.jsonl
blind_packet_manifest.json
entity_resolution.jsonl
outcome_ledger.jsonl
research_episode.json
brain_delta.jsonl
source_ledger.jsonl
id_registry.json
phase_state.json
access_log.jsonl
phase_audit_report.json
validation_report.json
bundle_manifest.json
direct_ingest_contract.json
```

Markdown 표와 JSON의 숫자가 다르면 renderer bug로 본다.

```text
bundle_status = QUARANTINE_RENDERER_INCONSISTENCY
brain_eligible = false
ACCEPT_FULL 금지
```

## G7. validator 필수 hard checks

`validate_nslab_bundle.py`는 최소 다음 check_id를 실제 계산해야 한다.

```text
schema_contract_verified
required_marker_count_verified
json_parse_all_verified
jsonl_parse_all_verified
input_sha256_verified
input_row_count_verified
csv_control_char_count_zero
research_daily_access_sha_verified
blind_snapshot_sha_verified
outcome_snapshot_sha_verified
row_disposition_count_equals_news_rows
outcome_ledger_count_equals_outcome_rows
final_watchlist_size_lte_20
final_watchlist_rank_sequence_1_to_N
final_watchlist_max_rank_lte_20
final_watchlist_duplicate_ticker_zero
final_candidate_source_fact_present
weak_final_reason_zero
blind_report_phase_leak_zero
embedded_blind_report_hash_match
separator_count_equals_1
id_registry_duplicate_zero
id_registry_orphan_reference_zero
source_reference_missing_zero
fact_quote_found_count_equals_fact_count
fact_quote_offset_verified
accepted_issuer_false_positive_zero
prefix_or_substring_binding_zero
group_brand_venue_product_generic_binding_zero
issuer_day_dedup_verified
direct_event_case_count_verified
brain_delta_record_count_verified
training_eligible_record_count_verified
theme_hindsight_separation_verified
retrospective_theme_outcome_only_training_zero
leader_pair_direction_verified
postmortem_correction_semantic_consistency_verified
placeholder_token_count_zero
incomplete_sentence_count_zero
canonical_graph_consistency_verified
validator_expected_source_not_generated_output
cross_block_input_coverage_consistency_verified
cross_block_population_count_consistency_verified
renderer_validator_independence_verified
final_candidate_generic_thesis_count_zero
final_candidate_economic_mechanism_present
access_log_present
phase_state_transition_valid
blind_seal_receipt_verified_before_outcome_access
preseal_outcome_download_count_zero
preseal_outcome_content_access_count_zero
preseal_outcome_label_access_count_zero
preseal_outcome_stat_count_zero
preseal_outcome_read_bytes_count_zero
preseal_outcome_sha256_count_zero
preseal_outcome_header_read_count_zero
preseal_outcome_row_count_count_zero
preseal_outcome_parse_count_zero
preseal_outcome_print_count_zero
preseal_outcome_used_in_blind_graph_count_zero
blind_report_access_log_parity_verified
markdown_final_watchlist_table_count_consistency_verified
markdown_final_watchlist_size_lte_20
direct_ingest_contract_present
direct_ingest_contract_json_valid
direct_brain_ingest_ready_computed_by_validator
direct_ingest_contract_count_hash_parity_verified
direct_ingest_contract_fatal_blockers_empty_if_accept_full
final_semantic_audit_present
final_semantic_audit_count_equals_final_watchlist_size
final_candidate_semantic_audit_id_present_all
final_candidate_semantic_audit_backref_valid_all
final_candidate_semantic_audit_status_pass_all
semantic_type_fixture_count_min_9
semantic_type_fixture_failure_count_zero
hard_check_boolean_type_mismatch_count_zero
hard_check_count_boolean_conflation_count_zero
repair_loop_executed_or_not_needed
```

다음 중 하나라도 있으면 `ACCEPT_FULL` 금지:

```text
누락된 hard check
expected가 생성 결과에서 복사된 check
actual/expected가 null인 critical check
critical check failed
validator_exit_code != 0
critical_error_count > 0
direct_brain_ingest_ready != true
requires_manual_research_review == true
requires_posthoc_prompt_repair == true
requires_human_semantic_review == true
```

## G8. 20260622 fixture 구조 회귀검사

가능하면 `docs/20260622_nslab_episode_bundle.example.md`를 열어 형식 회귀검사 fixture로 사용한다.
비교 대상은 숫자값 자체가 아니라 구조다.

반드시 확인할 것:

```text
schema_version == nslab.research_bundle.v11
artifact_type == research_episode_bundle
final_watchlist size <= 20
front matter에 canonical_graph_sha256 존재
renderer_version 존재
validator_version 존재
bundle_manifest에 fact_record_count 존재
issuer_day_case_count 존재
direct_event_case_count 존재
brain_delta_record_count 존재
training_eligible_record_count 존재
validation_report에 critical_error_count == 0
brain_delta가 record-level population을 가진다
```

fixture와 다른 구조를 만들 경우 `schema_deviation_report`에 기록한다.
critical field deviation이면 `ACCEPT_FULL` 금지다.

## G9. CSV 확보·정화 preflight

선택 CSV는 반드시 byte로 확보하고 전체 파싱한다.

성공 조건:

```text
local_csv_basename == selected_input_file
byte_size > 0
sha256 계산 완료
columns == page,row,date,time,title,body
CSV full parse 성공
row_count > 0
min/max published_at 계산 성공
time_unverified_rows 계산 완료
ESC(0x1b) count == 0
ETX(0x03) count == 0
tab/LF/CR 제외 C0 control char count == 0
```

제어문자가 있으면:

```text
1. 원본 sha256과 control char count를 audit에 기록
2. ESC/ETX 및 허용되지 않는 C0 control char만 제거한 sanitized copy 생성
3. row_count와 columns가 동일한지 검증
4. sanitized sha256을 input_sha256으로 사용
5. sanitize_report를 source_ledger와 validation_report에 기록
```

CSV 확보 또는 full parse 실패 시:

```text
다른 날짜 CSV 사용 금지
샌드박스 잔존 파일 사용 금지
최신 CSV로 대체 금지
가격 snapshot 날짜로 뉴스 날짜 변경 금지
output은 QUARANTINE_INPUT_UNPARSED 또는 CSV_ACQUIRE_FAILED만 허용
ACCEPT_FULL 금지
```

## G10. context contamination guard

이 대화/세션에 이미 해당 D의 outcome snapshot 숫자, winner list, postmortem 결과가 노출되어 있으면 새 BLIND 연구로 `ACCEPT_FULL`을 만들 수 없다.

다음 중 하나라도 true이면:

```text
context_already_contains_D_outcome_before_formal_renderer_seal == true
D outcome rows were printed before BLIND seal
D winner census was discussed before BLIND seal
previous attempt’s postmortem for same D is present in context
```

처리:

```text
bundle_status = QUARANTINE_CONTEXT_CONTAMINATED
blind_valid = false
forecast_evaluation_eligible = false
brain_eligible = false
```

단, 이미 봉인된 이전 BLIND packet을 그대로 재사용하고 그 hash가 검증되면 `POSTMORTEM_ONLY_REPAIR`는 가능하다.
이 경우에도 새로 BLIND 후보를 고치지 않는다.

## G11. 최종 ACCEPT_FULL 판정식

`ACCEPT_FULL`은 다음 식으로만 결정한다.

```python
accept_full_allowed = (
    schema_contract_verified
    and csv_full_parse_complete
    and research_daily_access_verified
    and blind_snapshot_hash_verified
    and outcome_snapshot_hash_verified
    and blind_packet_hash_verified
    and embedded_blind_report_hash_match
    and final_watchlist_size <= 20
    and final_watchlist_rank_sequence_valid
    and final_watchlist_max_rank <= 20
    and final_watchlist_duplicate_ticker_count == 0
    and required_marker_count_verified
    and id_references_valid
    and source_references_valid
    and fact_quote_entailment_valid
    and weak_final_reason_count == 0
    and final_candidate_generic_thesis_count == 0
    and final_candidate_economic_mechanism_present
    and theme_hindsight_separation_valid
    and brain_delta_record_count_verified
    and cross_block_input_coverage_consistency_verified
    and cross_block_population_count_consistency_verified
    and renderer_validator_independence_verified
    and attempt_history_present
    and accepted_attempt.status == ACCEPTED_CLEAN
    and access_log_present
    and phase_state_transition_valid
    and blind_seal_receipt_verified_before_outcome_access
    and accepted_attempt.preseal_outcome_download_count == 0
    and accepted_attempt.preseal_outcome_content_access_count == 0
    and accepted_attempt.preseal_outcome_label_access_count == 0
    and accepted_attempt.preseal_outcome_stat_count == 0
    and accepted_attempt.preseal_outcome_read_bytes_count == 0
    and accepted_attempt.preseal_outcome_sha256_count == 0
    and accepted_attempt.preseal_outcome_header_read_count == 0
    and accepted_attempt.preseal_outcome_row_count_count == 0
    and accepted_attempt.preseal_outcome_parse_count == 0
    and accepted_attempt.preseal_outcome_print_count == 0
    and accepted_attempt.preseal_outcome_used_in_blind_graph_count == 0
    and semantic_contamination_seen_in_any_attempt == false
    and blind_report_access_log_parity_verified
    and markdown_final_watchlist_table_count_consistency_verified
    and markdown_final_watchlist_size <= 20
    and validator_expected_source_not_generated_output
    and sample_weight_validation_status == "passed"
    and issuer_day_weight_sum_mismatches == {}
    and direct_event_weight_sum_mismatches == {}
    and validator_exit_code == 0
    and critical_error_count == 0
    and context_contamination_count == 0
    and direct_ingest_contract_present
    and direct_ingest_contract_json_valid
    and direct_ingest_contract_count_hash_parity_verified
    and direct_brain_ingest_ready is True
    and requires_manual_research_review is False
    and requires_posthoc_prompt_repair is False
    and automated_import_expected_to_pass is True
    and direct_ingest_contract_fatal_blockers_count == 0
)
```

if `accept_full_allowed is not True`:

```text
bundle_status != ACCEPT_FULL
brain_eligible = false
```

`ACCEPT_FULL`, `brain_eligible`, `validator_exit_code`는 사람이 임의로 쓰지 않는다.
반드시 `validate_nslab_bundle.py`의 계산 결과를 그대로 사용한다.

## G12. 실패해도 거짓 성공보다 낫다

이 프롬프트의 목표는 언제나 `ACCEPT_FULL`이지만, 결함이 남은 `ACCEPT_FULL`은 가장 나쁜 결과다.
다음 우선순위를 따른다.

```text
1. 완전 검증된 ACCEPT_FULL
2. 원인과 ID가 분명한 QUARANTINE
3. CSV/가격 확보 실패 영수증
4. 검증되지 않은 ACCEPT_FULL 금지
```

최종 채팅 응답은 사용자의 별도 형식을 따르되, 생성된 실제 Markdown 파일 하나만 가리킨다.

## G13. Cross-block consistency hard guard

최근 smoke-run에서 사람용 보고서와 `blind_prediction.json`·`research_episode.json` 사이의 `input_coverage_warning`, `uncovered_time_ranges`, object count가 서로 다르게 기록될 수 있음이 확인됐다. 최종 bundle의 진실 원천은 `canonical_graph.json` 하나이며, 모든 Markdown·JSON·JSONL은 같은 canonical graph에서 렌더링되어야 한다.

다음 필드는 모든 artifact에서 동일해야 한다.

```text
trade_date
previous_trade_date
next_trade_date
window_start
cutoff_at
input_file
input_sha256
input_row_count
input_coverage_warning
uncovered_time_ranges
time_unverified_rows
row_disposition_count
candidate_screening_count
final_watchlist_count
outcome_ledger_count
issuer_day_case_count
direct_event_case_count
brain_delta_record_count
training_eligible_record_count
```

`input_coverage_warning` 판정식은 다음으로만 계산한다.

```python
expected_end = cutoff_at
actual_max = max(parsed_published_at)
tolerated_cutoff_gap_seconds = 180
cutoff_gap_seconds = (expected_end - actual_max).total_seconds() if actual_max else None

if actual_max is None:
    input_coverage_warning = True
    uncovered_time_ranges = [{"reason": "missing_max_published_at"}]
elif actual_max > expected_end:
    input_coverage_warning = True
    uncovered_time_ranges = [{"reason": "csv_contains_after_cutoff"}]
elif cutoff_gap_seconds > tolerated_cutoff_gap_seconds:
    input_coverage_warning = True
    uncovered_time_ranges = [{"reason": "csv_max_time_before_expected_cutoff"}]
else:
    # cutoff 직전 0~180초 정도의 feed gap은 허용 가능하다.
    input_coverage_warning = bool(time_unverified_rows)
    uncovered_time_ranges = []
    tolerated_cutoff_gap_seconds_recorded = cutoff_gap_seconds
```

`uncovered_time_ranges`가 어느 artifact에서라도 비어 있지 않으면 모든 artifact에서 `input_coverage_warning == true`여야 한다.
cutoff 직전 허용 gap으로 처리하려면 모든 artifact에서 `uncovered_time_ranges == []`이고 `tolerated_cutoff_gap_seconds_recorded`가 동일해야 한다.
coverage warning 자체는 연구 중단 사유가 아니지만, artifact 간 불일치는 renderer/validator 결함이다.

validator hard check:

```text
cross_block_input_coverage_consistency_verified
cross_block_population_count_consistency_verified
cross_block_final_watchlist_consistency_verified
cross_block_brain_delta_count_consistency_verified
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_CROSS_BLOCK_INCONSISTENCY
brain_eligible = false
ACCEPT_FULL 금지
```

## G14. Renderer와 validator 독립성 hard guard

`render_nslab_bundle.py`와 `validate_nslab_bundle.py`는 서로 다른 검증 책임을 가진다. renderer는 canonical graph를 출력으로 바꾸는 도구이고, validator는 이미 만들어진 출력과 내부 artifact를 독립적으로 읽어 오류를 찾는 도구다.

원칙:

```text
renderer_sha256 != validator_sha256
```

만약 한 파일 안에 두 entrypoint를 함께 넣어야 하는 환경이라면, 전체 파일 SHA 하나만 기록하지 말고 다음을 별도로 기록한다.

```text
combined_tool_sha256
renderer_entrypoint_sha256
validator_entrypoint_sha256
```

이 경우에도 다음은 반드시 성립해야 한다.

```text
renderer_entrypoint_sha256 != validator_entrypoint_sha256
validator는 renderer의 self-declared manifest를 expected로 사용하지 않음
validator는 final bundle과 내부 artifact를 다시 읽어 계산함
```

validator hard check:

```text
renderer_validator_independence_verified
validator_does_not_import_renderer_state
validator_expected_source_not_generated_output
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_VALIDATOR_NOT_INDEPENDENT
brain_eligible = false
ACCEPT_FULL 금지
```

## G15. Final watchlist thesis specificity hard guard

`final_watchlist`의 각 항목은 일반 후보 설명이 아니라 실제 장전 의사결정 label이다. 따라서 final item의 thesis와 `why_now`는 원문 fact·경제 메커니즘·실패조건이 모두 결속되어야 한다.

다음 문구는 final item의 `preopen_thesis`, `why_now`, `blind_reason`, `red_team_counterargument`에서 단독 또는 핵심 근거로 사용할 수 없다.

```text
issuer-specific 사건을 직접 FACT 범위에서만 관찰한다
cutoff 이전 원문 fact가 issuer-scoped catalyst로 확인되어 장전 후보군에 포함된다
좋은 회사뉴스가 상한가형 사건과 동일하지 않을 수 있다
The event may already be priced
P snapshot safe feature만으로 후보화했다
P시점 소형 시총
P시점 중소형 시총
P시점 거래 회전 존재
최근 5거래일 10% 이상 고가 이력
최근 급등 흔적을 연속성과 선반영 양쪽으로 본다
```

이 문구들은 candidate_screening의 중립 설명이나 red-team 보조 문장에는 쓸 수 있지만, final 선정의 핵심 이유가 될 수 없다.

각 final item은 반드시 다음 구조를 가진다.

```json
{
  "candidate_id": "CAND-...",
  "rank": 1,
  "ticker": "000000",
  "name": "회사명",
  "catalyst_type": "CONTRACT_ORDER | PRODUCT_COMMERCIALIZATION | BIO_STAGE_ADVANCE | CAPITAL_POLICY | STRATEGIC_INVESTMENT | NAMED_BENEFICIARY | OTHER_CONCRETE",
  "primary_fact_ids": ["FACT-..."],
  "primary_quote": "원문 최소 인용",
  "economic_mechanism": "매출·비용·마진·자본정책·허가확률·수급 중 무엇이 왜 바뀌는지",
  "why_now": "cutoff 이전 새 정보와 P snapshot 보조 feature가 어떻게 결합되는지",
  "red_team_counterargument": "해당 후보에 고유한 실패조건",
  "generic_text_signature": "sha256(normalized thesis/red-team)"
}
```

validator hard check:

```text
final_candidate_primary_fact_present
final_candidate_primary_quote_found
final_candidate_economic_mechanism_present
final_candidate_generic_thesis_count_zero
final_candidate_generic_redteam_reuse_lte_3
weak_final_reason_zero
```

동일한 `red_team_counterargument` 또는 `preopen_thesis`가 final_watchlist에서 3회 초과 반복되면 템플릿 복사로 간주한다.

위반 시:

```text
generic_final_thesis_count += 1
reused_final_redteam_signature_count += 1
critical_error_count += 1
bundle_status = QUARANTINE_GENERIC_FINAL_THESIS
brain_eligible = false
ACCEPT_FULL 금지
```

## G16. BLIND pre-seal outcome file access guard — v23 zero-contamination

BLIND 봉인 전에는 `outcome_snapshot_path`의 문자열, expected sha256, expected row_count만 볼 수 있다.

다음 작업은 모두 BLIND 봉인 이후에만 허용한다.

```text
outcome snapshot 파일 다운로드
outcome snapshot stat/byte_size 계산
outcome snapshot sha256 계산
outcome snapshot header 읽기
outcome snapshot row_count 계산
outcome snapshot pandas/csv parse
outcome snapshot의 high_return_pct, close_return_pct, upper_limit_touched, upper_limit_closed 등 가격 label 열람
outcome snapshot 파일 미리보기·head·tail·grep·sample 출력
```

이전 버전의 `RECOVERABLE_PRESEAL_OUTCOME_METADATA_TOUCH`는 v23 accepted attempt에서는 폐지한다.

이전 실행 잔존 파일이고 이번 실행에서 내용을 읽지 않았다면, BLIND 작업 전에 파일명을 기준으로만 quarantine 디렉터리로 이동한다. 이때 sha256, byte_size, header, row_count를 계산하지 않는다.

이번 실행에서 BLIND seal 전에 outcome 파일이 다운로드되거나 stat/byte_size/sha256/header/row_count/parse/sample/label 계산 중 하나라도 발생하면 다음으로 처리한다.

```text
context_already_contains_D_outcome = true
accepted_attempt_valid = false
bundle_status = QUARANTINE_PHASE_CONTAMINATED_PRESEAL_OUTCOME_TOUCH
blind_valid = false
forecast_evaluation_eligible = false
brain_eligible = false
direct_brain_ingest_ready = false
training_eligible_record_count = 0
ACCEPT_FULL 금지
```

validator hard check:

```text
preseal_outcome_download_count_zero
preseal_outcome_stat_count_zero
preseal_outcome_content_access_count_zero
preseal_outcome_label_access_count_zero
preseal_outcome_sha256_count_zero
preseal_outcome_header_read_count_zero
preseal_outcome_row_count_count_zero
preseal_outcome_parse_count_zero
preseal_outcome_print_count_zero
preseal_outcome_used_in_blind_graph_count_zero
access_log_preseal_outcome_logical_role_count_zero
```

위 check 중 하나라도 실패하면 `ACCEPT_FULL` 금지다.

## G17. Markdown final-table parity guard

기계 JSON만 올바르고 사람용 Markdown 표가 틀리면 gold bundle이 아니다. `research_report.md`, `blind_report.md`, `postmortem_report.md` 안의 final watchlist 표는 `blind_prediction.json.final_watchlist`와 동일한 ticker·rank·count를 가져야 한다.

규칙:

```text
report_final_watchlist_count == blind_prediction.final_watchlist_count
blind_report_final_watchlist_count == blind_prediction.final_watchlist_count
postmortem_final_watchlist_count == blind_prediction.final_watchlist_count
all_report_final_ranks == blind_prediction.final_ranks
all_report_final_tickers == blind_prediction.final_tickers
```

Markdown 안에서 `final_watchlist` 또는 `최종 장전 관심종목`으로 표시된 표가 20개를 초과하면 JSON이 정상이어도 `ACCEPT_FULL` 금지다.

validator hard check:

```text
markdown_final_watchlist_table_count_consistency_verified
markdown_final_watchlist_rank_ticker_consistency_verified
markdown_final_watchlist_size_lte_20
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_MARKDOWN_JSON_PARITY
brain_eligible = false
ACCEPT_FULL 금지
```

## G18. CSV acquisition fallback hard guard

GitHub Raw CSV 확보는 bash/curl/urllib/requests 성공을 필수로 요구하지 않는다.

다음 오류는 파일 부재가 아니다.

```text
curl: Could not resolve host
urllib.request.URLError Temporary failure in name resolution
socket.gaierror
NETWORK=caas_packages_only
```

이 오류만으로 `CSV_ACQUIRE_FAILED`, `MAIN_PROMPT_ACQUIRE_FAILED`, `INPUT_UNPARSED`를 선언하지 않는다.

같은 YYYYMMDD 파일에 대해 반드시 다음 순서로 재시도한다.

```text
1. GitHub blob page를 web/browser 계층으로 열어 파일명·크기·Raw/View raw 존재 확인
2. GitHub raw endpoint를 download tool 또는 파일 분석 도구로 저장
3. raw.githubusercontent endpoint를 download tool 또는 파일 분석 도구로 저장
4. GitHub Contents API raw media type 또는 codeload ZIP fallback 사용
5. 로컬 저장 후 sha256·byte_size·columns·row_count·min/max published_at·control char count 검증
```

GitHub blob HTML은 존재 확인용이며, CSV 본문으로 파싱하거나 연구 입력으로 사용하지 않는다.

성공 조건:

```text
local_csv_basename == selected_input_file
byte_size > 0
sha256 계산 완료
columns == page,row,date,time,title,body
CSV full parse 성공
row_count > 0
min/max published_at 계산 성공
time_unverified_rows 계산 완료
tab/LF/CR 제외 C0 control char count == 0
```

fallback 중 하나가 성공하면 `status = CSV_ACQUIRED`로 둔다.
`curl/urllib/requests` 실패 이력은 `acquisition_warnings`에만 기록한다.
모든 web/download fallback까지 실패한 경우에만 `CSV_ACQUIRE_FAILED`로 중단한다.
다른 날짜 CSV, 샌드박스 잔존 파일, 최신 CSV로 대체하지 않는다.

validator hard check:

```text
csv_acquisition_fallback_policy_verified
shell_dns_failure_not_treated_as_file_missing
selected_csv_basename_verified
sandbox_residual_file_not_used
```

위반 시:

```text
bundle_status = QUARANTINE_ACQUISITION_POLICY_VIOLATION
brain_eligible = false
ACCEPT_FULL 금지
```


이 프로토콜의 핵심은 “연구량”보다 “학습 가능한 의미 정확도”다.
원자 사실·추론·후보·결과 label의 경계를 보존하고, 근거 없는 feature나 사후 혼합 record를 두뇌에 넣지 않는다.

이 버전은 직전 프로토콜에서 이미 성공한 다음 항목을 그대로 유지한다.

```text
BLIND와 OUTCOME의 물리적 분리
research_daily P/D snapshot 계약
전 시장 outcome census
issuer-day 중복 제거
fact/inference 근거 잠금
sealed theme universe와 retrospective theme 분리
leader pair의 BLIND 선택과 outcome target 분리
중앙 ID registry
사람용 보고서의 BLIND/POSTMORTEM 경계
```

동시에 다음 재발 오류를 0건으로 만들기 위해 **canonical graph → deterministic renderer → executable validator → independent semantic auditor** 구조를 사용한다.

```text
회사명 prefix·substring 오결속
그룹·브랜드·장소·제품명을 상장사로 오인
일반명사를 상장사로 오인
보고서 placeholder 잔존
수동으로 다시 쓴 표와 JSON의 불일치
검증 boolean을 실제 계산 없이 true로 기록
잘린 템플릿·범용 fallback 문구
서로 다른 사건에 같은 사후 교정문을 복사
원 FACT에 없는 산업·고객·공급망 메커니즘을 사후 교훈에 삽입
같은 ticker가 이미 BLIND 후보에 있는데 다른 사건 누락을 issuer-level RANKING_MISS로 오분류
retrospective theme 구성 종목을 cutoff 이전 관계 근거 없이 training eligible로 승격
결과에서 함께 오른 종목이라는 이유만으로 수혜관계를 소급 생성
```

사용자가 선택한 `news_YYYYMMDD.csv`는 원칙적으로 다음 구간의 뉴스를 포함한다.

```text
직전 실제 거래일 15:30:00 KST 이후
~
연구 대상 실제 거래일 08:59:59 KST 이전
```

뉴스 입력의 기본 위치와 가격 연구 전용 저장소는 다음과 같다.

```text
news_repository_url = https://github.com/Daikisong/new_bot/tree/main/docs/csv
news_raw_base_url   = https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/

price_repository_url       = https://github.com/Daikisong/stock-web
research_daily_base_url    = https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily
repository_raw_base_url    = https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/
research_daily_manifest    = atlas/research_daily/manifest.json
research_daily_schema      = atlas/research_daily/schema.json
research_daily_calendar    = atlas/research_daily/trading_calendar.csv
research_daily_access_root = atlas/research_daily/access
```

가격 연구에는 반드시 `atlas/research_daily`만 사용한다.

이 계층은 다음 특성을 갖는 것으로 검증하고 사용한다.

```text
- 거래일별 KOSPI·KOSDAQ·KOSDAQ GLOBAL 전 시장 plain CSV snapshot
- BLIND용 직전 거래일 snapshot과 OUTCOME용 당일 snapshot의 분리
- 거래일별 access JSON에 안전한 경로·SHA-256·행 수 기록
- FinanceData/marcap 원시 비수정 OHLC
- 기업행위 의심 구간과 신규상장·기준가격 불명 구간의 라벨 차단
- KONEX 기본 제외
```

이 연구의 목적은 하루 추천문을 만드는 것이 아니다.

몇 년 동안 축적되는 episode를 Codex 기반 `news-scalping-lab` 연구 두뇌가 읽고 통합하여, 새로운 장전 뉴스 CSV를 받았을 때 다음을 일반화하도록 만드는 것이 목적이다.

- 어떤 직접 기업뉴스가 단일종목 상한가·급등을 만드는가
- 어떤 정책·산업·지역·글로벌 뉴스가 실제 주도섹터를 만드는가
- 어떤 직접·간접·시장기억 수혜주가 선택되는가
- 같은 테마에서 왜 특정 종목이 대장이 되고 다른 종목은 탈락하는가
- 좋은 기업뉴스인데도 왜 가격 반응이 약한가
- 전일 시장이 이미 선택한 대장과 최근 수급이 다음 거래일까지 이어지는 조건은 무엇인가
- 장전에는 예측할 수 없었던 상한가와, 뉴스가 있었는데 놓친 상한가를 어떻게 구분하는가
- 뉴스 자체가 없거나 cutoff 이후 사건으로 발생한 수급성 상한가를 어떻게 별도 분류하는가
- 후보 생성 실패와 후보 순위 실패를 어떻게 분리하는가

연구 결과는 사람이 읽는 보고서와 기계가 수입할 수 있는 구조화 데이터를 함께 담은 단일 Markdown 번들로 남긴다.


## G19. ACCEPT_FULL target repair loop hard guard

`ACCEPT_FULL`은 가짜 성공 선언이 아니라 자동 import-ready 상태의 최종 계산 결과다. 이번 실행은 가능한 한 `ACCEPT_FULL`을 목표로 자동 수리하되, 수리 불가능한 오염을 숨기지 않는다. 단, “수리 불가능”은 실제 access_log·validator·phase_state로 증명되어야 하며, 작업량이 많거나 어렵다는 이유로 추정하지 않는다.

자동 수리 원칙:

```text
- gate를 낮추지 않는다.
- expected 값을 결과물에 맞춰 바꾸지 않는다.
- Markdown을 손으로 고치지 않고 canonical_graph를 수정한 뒤 재렌더링한다.
- 수리 후 항상 validator를 재실행한다.
- validation_report, bundle_manifest, direct_ingest_contract를 마지막 검증 결과로 다시 쓴다.
```

수리 가능한 오류:

```text
renderer/validator 물리 분리 실패
final_watchlist 20개 초과
rank gap 또는 duplicate ticker
source_fact_ids 누락
weak final reason
score/rank semantics 누락
brain_delta record population 부족
Markdown/JSON count 불일치
ID/source/fact orphan
placeholder 또는 incomplete sentence
direct_ingest_contract count/hash 불일치
issuer_day 또는 direct_event sample_weight 합계 불일치
```

수리 불가능한 오류:

```text
BLIND seal 전 outcome snapshot 실제 내용 접근: data row parse/sample 출력/high_return/close_return/upper_limit/winner census 계산
같은 D outcome을 이미 본 context에서 새 BLIND 생성
CSV full parse 실패
research_daily access 또는 snapshot hash 검증 실패
공식 비거래일
```

수리 가능한 오류는 최대 16회까지 자동 수리한다. 수리 불가능한 오류는 같은 실행에서 `ACCEPT_FULL`로 만들지 않고 명시적 QUARANTINE으로 종료한다.

validator hard check:

```text
repair_attempt_count <= 16
repair_loop_executed_or_not_needed == true
remaining_fixable_critical_error_count == 0
unsafely_downgraded_critical_error_count == 0
quarantine_if_unrepairable_error == true
```

위반 시:

```text
critical_error_count += 1
bundle_status = QUARANTINE_REPAIR_CONTRACT_VIOLATION
brain_eligible = false
direct_brain_ingest_ready = false
ACCEPT_FULL 금지
```
────────────────────────────────────────
0. 절대 불변 원칙
────────────────────────────────────────

## 0.1 결과는 금지 대상이 아니라 정답 라벨이다

거래일 D의 상한가 연구를 하려면 D 결과를 반드시 본다.

올바른 순서는 다음과 같다.

```text
장전 뉴스와 D-1 안전 시장정보 X만 사용해 BLIND 모집단·후보·순위를 완성
→ BLIND 패킷의 모든 파일을 실제로 저장·해시·봉인
→ 봉인된 BLIND 파일을 절대 수정하지 않음
→ 그 뒤에만 거래일 D 전 시장 outcome snapshot Y를 다운로드·열람
→ X와 Y를 결합해 성공·실패·반례 연구
```

잘못은 D 결과를 보는 것이 아니다.

잘못은 D 결과를 본 뒤 다음을 수정하는 것이다.

```text
행 분류
엔티티 승인·거절
직접뉴스 관측 모집단
후보 포함·제외 결정
후보 순위
섹터 가설
테마 수혜주 후보
연속성 후보
BLIND 대장 비교
```

이번 episode는 반드시 다음을 서로 분리해 만든다.

```text
BLIND forecast record
결과를 보기 전에 실제로 무엇을 관측·예측했는가

SUPERVISED population record
장전 모집단 전체와 거래일 D 결과가 어떻게 연결됐는가

RETROSPECTIVE discovery record
결과를 보고 발견한 누락 경로·새 수혜관계·오류는 무엇인가
```

## 0.2 가격 접근은 research_daily 계약으로만 수행한다

과거 실패에서 사용한 다음 경로를 이번 연구에 사용하지 않는다.

```text
atlas/symbol_profiles
atlas/universe/all_symbols.csv
atlas/universe/current_symbols.csv
latest_close
latest_marcap
종목별 연도 shard를 수천 번 개별 다운로드
FinanceData/marcap parquet 직접 다운로드
포털 TOP30을 전 시장 결과로 대체
임의의 현재가·차트·시세 페이지
```

거래일 D의 가격 접근은 반드시 다음 순서로만 수행한다.

```text
1. research_daily manifest·schema·trading calendar 확인
2. access/YYYY/MM/YYYYMMDD.json 다운로드
3. BLIND 봉인 전에는 access의 blind_snapshot_path만 다운로드
4. BLIND 패킷 봉인 완료
5. 봉인 재검증
6. 그 뒤에만 access의 outcome_snapshot_path 다운로드
```

`access JSON`에는 가격 숫자가 없고 안전 경로·날짜·해시·행 수만 있으므로 BLIND 전에 열 수 있다.

`outcome_snapshot_path`의 문자열을 아는 것은 오염이 아니지만, 해당 파일의 바이트·행·숫자를 BLIND 봉인 전에 다운로드·열람·미리보기·출력하는 것은 오염이다.

## 0.3 BLIND 패킷 전체를 봉인한다

`blind_prediction.json` 하나만 봉인해서는 안 된다.

다음 BLIND 논리 파일 전체를 결과 공개 전에 저장·해시·봉인한다.

```text
blind_prediction.json
blind_report.md
row_disposition.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
final_semantic_audit.jsonl
market_state_override_audit.jsonl
body_table_candidate_generation_audit.jsonl
ledger_population_audit.json
outcome_leader_census.jsonl
outcome_to_news_audit.jsonl
blind_packet_manifest.json
```

결과 확인 뒤 위 파일을 재작성·보강·정정하지 않는다.

사후 교정은 반드시 별도의 다음 파일에만 기록한다.

```text
entity_resolution.jsonl
outcome_ledger.jsonl
brain_delta.jsonl
research_episode.json
research_report.md의 POSTMORTEM 구간
```

## 0.4 적격성을 하나로 뭉개지 않는다

다음을 각각 독립적으로 기록한다.

```text
forecast_evaluation_eligible
direct_population_training_eligible
direct_record_training_eligible
issuer_day_training_eligible
theme_formation_training_eligible
beneficiary_discovery_training_eligible
blind_leader_pair_training_eligible
retrospective_pair_training_eligible
candidate_generation_error_training_eligible
entity_error_training_eligible
retrospective_memory_eligible
brain_eligible
```

일부 레코드만 정확하다고 전역 통계를 완전하다고 선언하지 않는다.

반대로 전역 게이트 하나가 실패했다고 정확한 개별 레코드까지 모두 버리지 않는다.

## 0.5 raw/unadjusted 가격의 의미를 정확히 지킨다

`research_daily`는 FinanceData/marcap의 원시 비수정 OHLC다.

다음 원칙을 지킨다.

```text
corporate_action_warning == true
또는
upper_limit_label_status가 verified_normal_day가 아님
또는
new_listing_or_no_reference == true
또는
data_quality_status가 blocked_* 상태
```

인 행은 상한가·수익률 감독학습의 정상 라벨로 사용하지 않는다.

이 경우 false로 바꾸지 말고 `QUARANTINED_PRICE_LABEL` 또는 명시된 차단 상태로 보존한다.

snapshot이 이미 제공하는 다음 필드를 우선 사용한다.

```text
open_gap_pct
high_return_pct
low_return_pct
close_return_pct
turnover_pct
limit_up_price
upper_limit_touched
upper_limit_closed
upper_limit_released
one_price_upper_limit
upper_limit_label_status
corporate_action_warning
new_listing_or_no_reference
data_quality_status
```

차단된 라벨을 임의 공식이나 29.x% 임계치로 다시 살려내지 않는다.

## 0.6 BLIND에서 일반 웹검색을 하지 않는다

PHASE A BLIND에서는 다음만 사용한다.

```text
선택된 뉴스 CSV
검증된 access JSON
검증된 blind snapshot(P)
available_from <= D인 이전 clean 연구기억
로컬 거래일 캘린더 또는 research_daily trading calendar
일반 경제·산업 인과 추론
```

BLIND 중 일반 웹검색, 현재가 조회, 뉴스 재검색, 기사 URL 재열람을 하지 않는다.

```text
blind_web_search_call_count = 0
blind_current_price_access_count = 0
blind_outcome_snapshot_download_count = 0
```

POSTMORTEM에서는 웹검색을 허용하되 게시시각을 검증하고, cutoff 이후 자료를 BLIND 근거로 소급하지 않는다.

## 0.7 엔티티 의미 정확도가 단순 명사 추출보다 우선한다

모든 행을 분류하는 것과 모든 명사구를 회사로 추출하는 것은 다르다.

다음을 절대 하지 않는다.

```text
제목의 쉼표 앞 문자열을 자동 회사명으로 사용
따옴표 안 문장을 회사명으로 사용
모든 고유명사를 회사로 사용
사람·선수·정치인·스포츠팀·학교·정부기관·지자체를 상장사로 사용
지역명·제품명·서비스명·문장 조각을 회사로 사용
외국기업·비상장사를 한국 상장사 직접 사건으로 사용
그룹명·브랜드명을 자동으로 특정 상장 모회사에 연결
기사 어디엔가 있는 6자리 코드를 다른 회사에 전파
```

BLIND의 상장사 검증에는 `blind snapshot(P)`의 날짜 시점 회사명·코드·시장만 사용한다.

현재 최신 회사명·현재 active universe를 과거 날짜에 소급하지 않는다.

## 0.8 모든 직접 기업뉴스는 후보 심사를 받는다

Issuer Entity Gate를 통과한 모든 고유 event-company observation은 반드시 `candidate_screening.jsonl`에 정확히 한 번 등장해야 한다.

다음을 금지한다.

```text
애널리스트 기사라는 이유만으로 심사 없이 제외
시장전망 기사라는 이유만으로 observation 미생성
최종 watchlist에 들지 않았다는 이유로 모집단에서 삭제
broad theme 기사 안의 구체 회사 문장을 놓침
```

리포트·전망 기사도 다음이 있으면 직접 심사한다.

```text
구체 고객점유율
공급량 증가
제품별 병목
실적 bridge
고객 인증·평가
신규 수요처
구체적인 제품·공정 연결
```

출처 유형 자체가 아니라 내용의 직접성·신규성·경제가치 귀속을 판단한다.

## 0.9 과거 연구는 허용목록이 아니다

과거에 동일 키워드·동일 종목이 없다는 이유로 후보를 버리지 않는다.

```text
현재 사건을 먼저 오픈월드 방식으로 해석
→ 작동 메커니즘과 수혜 경로 생성
→ 과거 clean 연구는 지지·반박·확장 증거로 사용
```

과거 연구 검색 실패는 후보 탈락 사유가 아니다.

## 0.10 코드식 시장법칙을 만들지 않는다

다음과 같은 단순 규칙을 생성하지 않는다.

```text
세계 최초 = 강한 호재
국책과제 = 5점
MOU = 1점
공급계약 = 무조건 상한가
지역명 = 고정 종목
정책명 = 고정 섹터
```

대신 조건부 메커니즘·적용조건·실패조건·반례를 남긴다.

## 0.11 순차 연구와 세션 문맥

연구는 거래일 오름차순으로 진행한다.

현재 D의 BLIND에는 다음만 사용할 수 있다.

```text
episode.available_from <= D
```

현재 D보다 뒤 날짜의 결과·교훈이 대화에 존재하면:

```text
context_order_status = OUT_OF_ORDER_CONTEXT_RISK
forecast_evaluation_eligible = false
```

동일 D의 결과를 이미 본 동일 세션에서 재실행하면:

```text
context_already_contains_D_outcome = true
status = RETROSPECTIVE_ONLY
forecast_evaluation_eligible = false
```

## 0.12 가격 결과의 학습 단위는 issuer-day다

같은 종목이 같은 날 뉴스 15건에 등장해도 가격 결과 1개를 15개의 독립 표본으로 계산하지 않는다.

다음을 모두 보존한다.

```text
개별 event-company 관계
각 뉴스 사건의 특징
하나의 issuer_day_case
```

기본 감독학습 단위:

```text
issuer_day_case_id = <TRADE_DATE>:<TICKER>
```

동일 issuer-day의 event가 N개라면 다음 중 하나를 명시적으로 사용한다.

```text
권장: issuer-day 하나로 병합하고 event_ids를 배열로 보존
보조 event-level record: sample_weight = 1 / N
```

한 종목 하루 결과의 총 sample weight는 1을 초과하지 않는다.

여러 사건 중 무엇이 가격을 움직였는지 독립적으로 식별할 수 없으면:

```text
attribution_status = MULTI_EVENT_ATTRIBUTION_AMBIGUOUS
```

로 기록하고 각 사건을 독립 원인처럼 단정하지 않는다.


## 0.13 원자 사실 우선·의미 특징 근거 잠금

이 버전의 가장 중요한 추가 원칙이다.

두뇌에 들어가는 뉴스 특징은 자유로운 태그나 템플릿 문구가 아니라, 입력 CSV의 정확한 문장에 근거한 **원자 사실(atomic fact)** 이어야 한다.

다음을 금지한다.

```text
기사에 없는 AI·글로벌고객·상용화·수주·승인 특징을 관성적으로 부여
다른 event의 특징을 현재 event로 복사
같은 산업이라는 이유만으로 제품·고객·계약 특징을 전파
후보 설명용 상투 문구를 감독학습 feature로 사용
근거 없는 고정 feature tag를 빈칸 채우기 용도로 생성
문장이 중간에서 잘린 상태를 정상 feature로 저장
```

BLIND에서 다음 두 장부를 별도로 만든다.

```text
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
```

### Atomic Fact

각 fact는 반드시 원문 exact span을 가진다.

```json
{
  "fact_id": "FACT-000001",
  "row_id": "NEWS-000001",
  "entity_id": "ENT-000001",
  "observation_id": "OBS-000001",
  "event_id": "EVT-000001",
  "subject_literal": "",
  "predicate_statement": "",
  "object_or_value": "",
  "qualifiers": [],
  "temporal_expression": null,
  "modality": "CONFIRMED | ANNOUNCED | PLANNED | NEGOTIATING | ANALYST_VIEW | UNCLEAR",
  "exact_quote": "",
  "quote_char_start": 0,
  "quote_char_end": 0,
  "source_ids": [],
  "extractor_confidence": "high | medium | low",
  "verifier_decision": "ENTAILED | NOT_ENTAILED | AMBIGUOUS",
  "verifier_reason": ""
}
```

`exact_quote`는 해당 사실을 직접 뒷받침하는 최소 문장 또는 짧은 연속 구간이어야 한다.

fact 생성 시 event cluster가 아직 확정되지 않았다면 `event_id=null`로 두고, BLIND 사건 군집화가 끝난 뒤 봉인 전에만 유효 event_id를 배정한다. outcome 이후에는 fact의 event_id를 변경하지 않는다.

다음 조건을 모두 만족한 fact만 학습 입력으로 사용할 수 있다.

```text
verifier_decision == ENTAILED
exact_quote가 실제 row 본문에 정확히 존재
quote offset이 실제 문자열 경계와 일치
fact의 row_id가 event의 input_row_ids에 포함
fact의 entity_id가 해당 observation의 entity_id와 일치
다른 event·다른 issuer의 사실을 가져오지 않음
```

### Blind Inference

경제적 의미·시장 서사·수혜 경로처럼 원문에 직접 쓰이지 않은 해석은 fact가 아니라 inference다.

```json
{
  "inference_id": "INF-000001",
  "statement": "",
  "inference_type": "ECONOMIC_MECHANISM | MARKET_NARRATIVE | BENEFICIARY_PATH | RISK | OTHER",
  "supporting_fact_ids": [],
  "scope_event_ids": [],
  "scope_entity_ids": [],
  "uncertainty": "low | medium | high",
  "verifier_decision": "SUPPORTED | WEAKLY_SUPPORTED | UNSUPPORTED",
  "verifier_reason": ""
}
```

`UNSUPPORTED` inference는 후보 설명에 경고로 남길 수 있으나 training feature로 사용할 수 없다.

모든 candidate·theme·screening·Brain Delta는 자유로운 feature object를 새로 만들지 말고 다음만 참조한다.

```text
supporting_fact_ids
supporting_inference_ids
safe_D1_feature_fields
```

근거가 없으면 빈 배열을 사용한다. 그럴듯한 문구를 채우지 않는다.

## 0.14 독립 의미 검증과 cross-event 누수 차단

fact·inference·후보 thesis를 만든 작성 패스와 별도의 독립 검증 패스를 수행한다. 검증 패스에는 작성자의 자유 설명을 주지 말고, 원문 row·exact quote·검증 대상 statement·관련 ID만 제공한다.

검증기는 다음을 확인한다.

```text
feature statement가 exact quote로부터 실제로 도출되는가
주어 회사가 바뀌지 않았는가
제품·고객·계약·금액·단계가 다른 event에서 유입되지 않았는가
분석가 전망을 회사 확정 사실로 승격하지 않았는가
전체 사업비를 회사 귀속액으로 바꾸지 않았는가
협의·예정·추진을 계약 완료로 바꾸지 않았는가
상장 모회사·자회사·브랜드·그룹을 임의 전파하지 않았는가
```

최종 게이트:

```text
training_eligible semantic feature 중 NOT_ENTAILED == 0
training_eligible inference 중 UNSUPPORTED == 0
cross_event_feature_leak_count == 0
cross_issuer_feature_leak_count == 0
feature_without_fact_or_inference_reference_count == 0
```

하나라도 실패하면 자동 수리 후 재검증한다. 해결되지 않으면 해당 record는 `training_eligible=false`이며 `ACCEPT_FULL`을 금지한다.

## 0.15 중앙 ID Registry와 참조 무결성

모든 ID를 최종 조립 전에 중앙 registry에 등록한다.

```text
id_registry.json
```

등록 대상:

```text
NEWS row
SOURCE
ENTITY
FACT
INFERENCE
EVENT
OBSERVATION
SCREENING
THEME
CANDIDATE
BLIND_PAIR
ISSUER_DAY_CASE
BRAIN_DELTA_RECORD
```

규칙:

```text
각 ID는 전 bundle에서 정확히 한 번 정의
정의되지 않은 ID 참조 금지
삭제한 객체의 ID를 다른 블록에 남기지 않음
row_disposition.event_id는 null 또는 실제 event_clusters의 ID
모든 source_id는 source_ledger에 존재
모든 candidate_id는 blind_prediction에 존재
모든 pair candidate는 sealed candidate pool에 존재
모든 fact의 row·entity·observation·event 참조가 존재
```

`id_registry.json`을 바탕으로 모든 JSON·JSONL을 재귀 순회해 참조 검사를 코드로 수행한다.

```text
duplicate_defined_id_count == 0
orphan_reference_count == 0
wrong_id_type_reference_count == 0
source_reference_missing_count == 0
```

자기 선언으로 `id_references_valid=true`를 쓰지 않는다. 실제 검사 결과만 기록한다.

## 0.16 사람용 보고서도 BLIND와 POSTMORTEM을 물리적으로 분리한다

사람용 보고서의 BLIND 구간에 D 결과가 섞이는 문제를 방지하기 위해 다음 두 파일을 만든다.

```text
blind_report.md
postmortem_report.md
```

`blind_report.md`는 outcome snapshot을 열기 전에 작성·저장·해시·봉인한다.

`blind_report.md`와 `blind_packet_manifest.json`의 seal timestamp 이전에는 outcome snapshot 파일의 존재 감사와 access JSON metadata 확인을 제외한 모든 outcome 파일 내용 접근을 금지한다.

최종 `research_report.md`는 다음의 단순 결합이어야 한다.

```text
봉인된 blind_report.md의 원문 바이트
+ 정확한 구분선
+ postmortem_report.md의 원문 바이트
```

구분선:

```text
--- BLIND 봉인 이후 결과 공개 ---
```

결과 공개 뒤 BLIND 보고서의 표·순위·설명에 D 고가·D 종가·response·적중 여부를 덧붙이지 않는다.

BLIND 보고서 금지 필드:

```text
D_open
D_high
D_low
D_close
D_amount
D_turnover
D 고가
D 종가
actual_outcome
response_class
upper_limit_touched on D
상한가 적중
```

D-1 정보는 `P_` 접두사로 명시한다.

최종 검사:

```text
embedded_blind_report_sha256 == sealed_blind_report_sha256
report_phase_leak_count == 0
separator_count == 1
```

## 0.17 순위·텍스트 완전성·자동 수리

최종 watchlist의 rank는 반드시 연속된 정수다.

```text
rank 집합 == 1..N
중복 rank == 0
누락 rank == 0
후보 중복 == 0
```

텍스트 필드는 완전한 문장으로 끝나야 한다.

다음을 금지한다.

```text
문장 중간 절단
영문 템플릿이 중간에서 끊긴 문자열
placeholder
TODO
TBD
...
설명 없이 복사된 범용 문구
```

허용되지 않는 예:

```text
issuer-specific analyst evidence is concrete enough to screen, but it is not a newly confi
confirmed and issuer-attributable fact with a visible commercial, regulatory, order, or ca
```

정보가 부족하면 문장을 억지 완성하지 말고 `null`, 빈 배열, 또는 명시적 `UNRESOLVED`를 사용한다.

최종 산출물 전 코드 validator를 실행하고 최대 16회 자동 수리한다.

수리 대상:

```text
semantic entailment 오류
cross-event 특징 누수
ID orphan
source orphan
rank gap
보고서 phase leak
theme hindsight 혼합
leader pair label 방향 오류
중복 issuer-day weight
불완전 문장
manifest와 실제 블록 불일치
```

16회 후에도 critical error가 남으면 `ACCEPT_FULL`을 선언하지 않는다. 오류가 있는 Brain Delta를 두뇌에 넣는 것보다 `QUARANTINE`이 우선이다.


## 0.18 하나의 canonical research graph만 진실 원천으로 사용한다

BLIND와 POSTMORTEM의 기계 객체를 여러 파일에서 따로 작성하지 않는다.

먼저 내부 작업 디렉터리에 하나의 canonical research graph를 만든다.

```text
canonical_graph.json
```

이 graph는 다음 객체와 관계를 한 번만 정의한다.

```text
NEWS ROW
SOURCE
ENTITY MENTION
ISSUER BINDING
FACT
INFERENCE
EVENT
OBSERVATION
SCREENING
THEME
CANDIDATE
BLIND PAIR
OUTCOME
ISSUER-DAY CASE
BRAIN DELTA RECORD
```

규칙:

```text
1. ID는 canonical graph에서 한 번만 발급한다.
2. JSON·JSONL·사람용 표는 canonical graph에서 렌더링한다.
3. 같은 숫자·문장·순위를 보고서와 JSON에 수동으로 두 번 입력하지 않는다.
4. 객체를 삭제·병합하면 registry와 모든 참조를 먼저 갱신한다.
5. BLIND seal 뒤 canonical graph의 BLIND namespace는 읽기 전용이다.
6. POSTMORTEM은 별도 namespace에만 추가한다.
7. 최종 artifact 간 차이는 renderer의 버그로 간주한다.
```

최종 bundle에 canonical graph 전체를 중복 삽입할 필요는 없지만, 다음은 manifest에 기록한다.

```text
canonical_graph_sha256
canonical_graph_object_counts
renderer_version
renderer_sha256
```

## 0.19 기계 검증 결과는 LLM 자기 선언이 아니라 실행 코드가 계산한다

다음 파일을 내부 작업 디렉터리에 실제 생성하고 실행한다.

```text
validate_nslab_bundle.py
```

검증기는 draft bundle과 모든 내부 논리 artifact를 직접 읽어 다음을 계산한다.

```text
JSON·JSONL 파싱
마커 개수
파일·블록 SHA-256
ID 정의·참조
source 참조
rank 연속성
issuer-day 중복
entity binding 규칙
fact quote·offset·entailment
cross-event·cross-issuer 누수
BLIND report outcome 누수
sealed theme hindsight 혼합
leader pair target 방향
사후 오류 교훈의 사건·사실 의미정합성
generic correction의 cross-signature 재사용
동일 ticker의 issuer/event 오류 scope 분류
retrospective theme member별 cutoff provenance
outcome-only theme relation의 training 승격
placeholder·template token
잘린 문장·fallback 문구
manifest와 실제 count·hash 일치
```

다음을 금지한다.

```text
LLM이 계산 없이 validation boolean을 true로 작성
manifest의 self-declared true를 검증 근거로 사용
오류 count를 null로 두고 passed 처리
샘플 몇 개만 검사하고 전수 통과로 선언
```

`validation_report.json`에는 반드시 다음을 기록한다.

```text
validator_version
validator_sha256
executed_at
exit_code
checked_artifact_hashes
각 check의 actual_count
각 오류의 artifact·JSON path·ID
repair_attempt_count
repair_history
```

`exit_code != 0`이면 `ACCEPT_FULL`과 `brain_eligible=true`를 금지한다.

## 0.20 renderer가 보고서와 최종 bundle을 생성한다

사람용 Markdown 표와 설명에 숫자를 직접 보간하지 않는다.

```text
render_nslab_bundle.py
```

를 내부에서 생성·실행하여 다음을 canonical graph에서 렌더링한다.

```text
blind_report.md
postmortem_report.md
research_report.md
blind_prediction.json
research_episode.json
각 JSONL ledger
id_registry.json
bundle_manifest.json
direct_ingest_contract.json
```

특히 다음 값은 renderer가 canonical object에서 직접 가져와야 한다.

```text
행 수·중복 수
사건 수·엔티티 수
후보 수·rank
상한가 census
Recall·Precision
eligibility count
hash·byte size
```

문서 템플릿의 미치환 변수는 BLIND seal 전과 최종 bundle 조립 후 각각 검사한다.

## 0.21 구현 결함은 ACCEPT_PARTIAL 사유가 아니다

다음은 외부 데이터 한계가 아니라 수리 가능한 구현 결함이다.

```text
placeholder 잔존
rank 누락·중복
ID orphan
source orphan
보고서 phase leak
잘린 템플릿
근거 없는 feature
cross-event·cross-issuer 누수
prefix·substring issuer 오결속
그룹·브랜드·장소·제품·일반명사 issuer 오인
leader pair target 방향 오류
sealed theme hindsight 혼합
사후 교정문과 원 FACT의 의미 불일치
서로 다른 사건에 generic correction 복사
동일 ticker가 이미 후보인데 issuer-level 누락으로 오분류
retrospective theme 종목별 cutoff 관계 provenance 누락
outcome-only 종목 관계를 training eligible로 승격
manifest와 실제 count·hash 불일치
```

이 중 하나라도 남아 있으면 `ACCEPT_PARTIAL`로 타협하지 않는다.

```text
자동 수리 성공 → 전체 validator 재실행
자동 수리 실패 → QUARANTINE
```

`ACCEPT_PARTIAL`은 다음처럼 연구자가 통제할 수 없는 외부 한계에만 허용한다.

```text
공식 거래일 outcome package 자체 부재
공식 최초 공개시각을 끝내 검증할 수 없음
기업행위로 가격 label이 구조적으로 차단됨
상장 여부·법인 동일성을 공식 출처로도 확정할 수 없음
```

정상 research_daily package가 존재하는 거래일에는 `ACCEPT_FULL`이 기대 상태다.

## 0.22 사후 오류 교훈은 사건·사실 국소성을 강제한다

사후 오류 레코드는 결과를 본 뒤 그럴듯한 일반론을 붙이는 자유 산문이 아니다.

각 오류 레코드는 **그 오류가 실제로 발생한 봉인된 행·엔티티·사건·관측·screening·후보**와, 사후에 확인한 정확한 결과·출처만으로 구성하는 구조화된 반사실(counterfactual) 기록이다.

다음 record_type에 이 계약을 적용한다.

```text
candidate_generation_error_case
candidate_ranking_error_case
event_thesis_selection_error_case
row_disposition_error_case
entity_resolution_error_case
counterexample
mechanism_memory
memory_claim
```

각 오류 레코드는 반드시 다음을 가진다.

```text
error_subject_scope = ROW | ENTITY | EVENT | ISSUER | THEME | LEADER
sealed_row_ids
sealed_entity_ids
sealed_event_ids
sealed_observation_ids
sealed_screening_ids
sealed_candidate_ids
semantic_basis_fact_ids
semantic_basis_inference_ids
postmortem_fact_ids
semantic_basis_source_ids
original_blind_state
verified_outcome_state
error_signature
correction_principle_clauses
counterfactual_action
same_ticker_present_in_blind_pool
same_ticker_present_in_final_watchlist
semantic_audit
```

`correction_principle_clauses`의 각 항목은 다음 구조를 가진다.

```json
{
  "clause_id": "CLAUSE-...",
  "text": "",
  "support_fact_ids": [],
  "support_inference_ids": [],
  "support_source_ids": [],
  "support_phase": "SEALED_BLIND | POSTMORTEM_VERIFIED",
  "same_event_or_explicit_comparison": true,
  "entailment_verdict": "ENTAILED | SUPPORTED | UNSUPPORTED"
}
```

다음은 금지한다.

```text
1. 같은 문장을 여러 오류 레코드에 기계적으로 복사
2. 원 FACT에 없는 AI·공급망·고객점유율·수출·병목·임상·정책 메커니즘 삽입
3. 투자주의·종가급변·상한가잔량 공지를 영업·AI·공급망 촉매로 재해석
4. CB·CPS·유상증자·최대주주 변경을 공급계약·산업 수혜 교훈으로 재해석
5. 원인을 확인하지 못한 가격 상승에 임의의 산업 설명 부여
6. 다른 event·다른 issuer의 FACT를 명시적 비교 레코드 없이 가져오기
7. 결과만 보고 “이런 특징을 봤어야 했다”는 미봉인 feature 생성
8. 특정 티커를 다음부터 포함하라는 종목 암기형 교훈
```

오류 유형은 다음 규칙으로 결정한다.

```text
ROW_CLASSIFICATION_MISS
= 입력 행이 가격 관련 사건인데 BLIND row disposition에서 누락·오분류

ENTITY_MISSING / ENTITY_FALSE_POSITIVE / TICKER_BINDING_ERROR
= 엔티티·상장사 동일성 단계의 오류

CANDIDATE_SCREENING_MISS
= observation과 screening은 존재하지만 사건 FACT를 잘못 읽어 INCLUDE/WATCH 후보로 올리지 못함

CANDIDATE_GENERATION_MISS
= screening이 후보 가능성을 인정했지만 concrete candidate를 만들지 못함

RANKING_MISS
= 해당 concrete candidate/ticker가 BLIND 후보 풀에 존재했으나 최종 순위에서 누락되거나 지나치게 낮았음

EVENT_THESIS_SELECTION_MISS
= 같은 ticker가 이미 최종 watchlist 또는 concrete pool에 있었지만, 실제 반응과 더 직접적으로 연결된 다른 event/thesis를 대표 논지로 선택하지 못함

EVENT_ATTRIBUTION_MISS
= 같은 issuer-day의 복수 사건 중 어느 사건의 귀속이 더 중요한지 잘못 선택하거나 불명확하게 처리함

MARKET_STATE_OR_CONTINUATION_CASE
= 투자주의·종가급변·상한가잔량·회전율·전일 상한가 같은 시장상태 신호

CAPITAL_ACTION_RESPONSE_CASE
= 유상증자·CB·CPS·감자·최대주주 변경·자사주·주식병합 등 자본행위 반응

NEWSLESS_OR_UNEXPLAINED
= cutoff 이전 검증 가능한 issuer·theme 촉매를 찾지 못함
```

**동일 ticker가 다른 event를 통해 final watchlist에 이미 존재하면 issuer-level `RANKING_MISS` 또는 “종목 누락”으로 기록하지 않는다.**

이 경우 반드시 `EVENT_THESIS_SELECTION_MISS` 또는 `EVENT_ATTRIBUTION_MISS`로 기록하고, 다음을 함께 보존한다.

```text
existing_watchlist_candidate_ids
selected_blind_event_ids
missed_more_relevant_event_ids
selected_thesis
alternative_thesis
outcome은 동일 issuer-day 공유 label이라는 사실
```

### 독립 사후 의미 감사

Brain Delta를 확정하기 전에 오류 레코드만 별도 입력으로 하는 독립 의미 감사 패스를 수행한다.

감사 입력은 다음으로 제한한다.

```text
해당 오류 레코드
해당 레코드가 참조한 봉인 FACT·Inference 원문
해당 레코드가 참조한 사후 FACT·출처
같은 ticker-day의 BLIND candidate 존재 여부
오류 taxonomy 정의
```

다른 사건의 설명문·다른 오류 레코드의 교정문은 감사 입력에 넣지 않는다.

감사 출력은 각 레코드마다 다음을 가진다.

```text
semantic_audit_verdict = PASS | FAIL
unsupported_concepts
cross_event_concepts
generic_template_suspected
error_scope_correct
error_type_correct
same_ticker_scope_correct
clause_support_complete
failure_reasons
```

`training_eligible=true`가 되려면 `semantic_audit_verdict == PASS`여야 한다.

정규화한 `correction_principle`이 둘 이상의 서로 다른 `error_signature`에 반복되면, 같은 메커니즘과 동일한 fact predicate 구조임을 독립 감사가 입증하지 않는 한 전부 FAIL이다.

## 0.23 retrospective theme은 종목별 cutoff 관계 provenance를 강제한다

결과 뒤 여러 종목이 함께 올랐다는 사실은 **테마 발견의 단서**일 뿐, 각 종목이 해당 테마의 수혜주였다는 장전 관계 증거가 아니다.

retrospective theme은 다음 두 층으로 분리한다.

```text
THEME-LEVEL HYPOTHESIS
결과 breadth를 보고 발견한 잠정 섹터 설명
기본 training_eligible = false
retrospective_memory_eligible = true 가능

MEMBER-LEVEL VERIFIED EDGE
각 종목과 사건·섹터의 관계가 cutoff 이전 공개 근거로 개별 검증된 경우
해당 edge만 beneficiary/theme-discovery 학습 가능
```

각 retrospective theme 구성 종목마다 반드시 `retrospective_theme_member_edge`를 생성한다.

필수 필드:

```text
edge_id
retrospective_theme_id
ticker
company_name
relation_class = DIRECT | FUNDAMENTAL | MARKET_MEMORY | CONTINUATION | INFERRED_NEW
relation_statement
relation_mechanism
source_ids
fact_ids
inference_ids
source_published_at
source_time_verified
available_before_cutoff
relation_known_at_cutoff
edge_origin = CSV_INPUT | PRIOR_CLEAN_MEMORY | POSTMORTEM_CUTOFF_SOURCE | AFTER_CUTOFF_SOURCE | OUTCOME_ONLY_ASSOCIATION
outcome_used_to_discover
outcome_used_as_relation_evidence
semantic_edge_audit_verdict
training_eligible
eligibility_reason
```

종목 edge가 학습 적격이 되려면 다음을 모두 만족해야 한다.

```text
source_ids 비어 있지 않음
모든 source_id가 source ledger에 존재
source_time_verified == true
source_published_at <= cutoff_at
available_before_cutoff == true
relation_known_at_cutoff == true
edge_origin이 AFTER_CUTOFF_SOURCE 또는 OUTCOME_ONLY_ASSOCIATION이 아님
outcome_used_as_relation_evidence == false
관계 문장이 source FACT 또는 prior clean memory에 의해 ENTAILED/SUPPORTED
semantic_edge_audit_verdict == PASS
```

결과에서 같이 오른 종목을 묶었지만 위 조건을 만족하지 못하면:

```text
training_eligible = false
eligibility_reason = OUTCOME_ONLY_OR_UNVERIFIED_THEME_MEMBER
```

으로 남긴다.

retrospective theme 자체에는 다음을 기록한다.

```text
all_observed_member_tickers
verified_cutoff_member_edge_ids
ineligible_member_tickers
member_edge_coverage_ratio
training_scope = HYPOTHESIS_ONLY | VERIFIED_MEMBER_EDGES_ONLY
forecast_hit = false
```

다음 규칙을 지킨다.

```text
1. retrospective theme 전체를 BLIND theme 적중으로 승격하지 않는다.
2. member_edge_coverage_ratio < 1이어도 검증된 edge 개별 학습은 가능하다.
3. 그러나 theme 전체 member population을 학습 표본으로 쓰려면 coverage ratio == 1이어야 한다.
4. cutoff 이후 기사·D 장중 공시·사후 “상승 이유” 기사만 있는 종목은 해당 날짜 장전 수혜주 학습에 사용하지 않는다.
5. broad sector 동반 상승만으로 FUNDAMENTAL 또는 MARKET_MEMORY edge를 만들지 않는다.
6. `blind_fact_ids=[]`, `source_ids=[]`, `time_verified=false`인 member를 training eligible로 둘 수 없다.
```

관계 edge에도 독립 의미 감사 패스를 수행한다.

감사 입력은 해당 종목·해당 관계 source만 포함하며, 결과 수익률과 다른 테마 종목의 이름은 관계 입증 자료로 사용하지 않는다.

────────────────────────────────────────
1. 날짜·거래일·비거래일 라우팅
────────────────────────────────────────

파일명보다 CSV 본문 시각과 research_daily 거래일 정보를 우선한다.

파일명의 `YYYYMMDD`와 CSV의 최대 게시 날짜를 D 후보로 사용한다.

거래일 D라면 다음 access URL을 구성한다.

```text
https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/access/YYYY/MM/YYYYMMDD.json
```

예:

```text
https://raw.githubusercontent.com/Daikisong/stock-web/refs/heads/main/atlas/research_daily/access/2026/06/20260622.json
```

access JSON을 실제 다운로드·파싱해 다음을 확정한다.

```text
trade_date = D
previous_trade_date = P
next_trade_date
blind_snapshot_date
blind_snapshot_path
outcome_snapshot_date
outcome_snapshot_path
blind_snapshot_sha256
outcome_snapshot_sha256
blind_snapshot_row_count
outcome_snapshot_row_count
blind_max_source_date
outcome_max_source_date
build_status
```

## 1.1 access 검증

필수:

```text
access.trade_date == D
access.previous_trade_date == P
access.blind_snapshot_date == P
access.outcome_snapshot_date == D
access.blind_max_source_date <= P
access.outcome_max_source_date <= D
access.build_status == complete
```

`next_trade_date`가 빈 값인 것은 최신 atlas 끝 날짜에서는 허용한다. 필요하면 거래일 캘린더로 계산하되 추측하지 않는다.

## 1.2 월요일·연휴 다음 거래일

현재 뉴스 CSV 하나가 이미 다음 전체 구간을 포함한다고 간주한다.

```text
직전 실제 거래일 P 15:30
~
현재 실제 거래일 D 08:59:59
```

주말·공휴일 CSV를 추가 병합하지 않는다.

## 1.3 공식 비거래일

D 후보의 access JSON이 없으면 즉시 휴장으로 단정하지 않는다.

`trading_calendar.csv`를 실제 다운로드·파싱한다.

```text
D가 calendar에 없음
→ 공식 비거래일

D가 calendar에 있음 + access JSON 없음
→ RESEARCH_DAILY_ACCESS_MISSING
```

공식 비거래일이면 일반 BLIND·OUTCOME·POSTMORTEM을 수행하지 않는다.

최소 Markdown 영수증 하나를 생성한다.

```text
artifact_type = deferred_non_trading_day
status = DEFERRED_NON_TRADING_DAY
brain_eligible = false
covered_by_next_trading_day_csv = true
```

파일명:

```text
<YYYYMMDD>_nslab_deferred_non_trading.md
```

## 1.4 research_daily 파일이 없는 거래일

공식 거래일인데 access 또는 snapshot이 없으면 휴장으로 취급하지 않는다.

뉴스-only BLIND는 수행·봉인할 수 있으나 다음으로 기록한다.

```text
status = COMPLETED_BLIND_PENDING_RESEARCH_DAILY
outcome_status = RESEARCH_DAILY_PACKAGE_MISSING
forecast_evaluation_eligible = false
```

기존 shard·latest 파일·포털 순위로 임시 outcome을 만들지 않는다.


────────────────────────────────────────
2. 단일 Markdown 산출물 계약
────────────────────────────────────────

사용자에게 제공하는 물리적 파일은 정확히 하나다.

거래일 파일명:

```text
<YYYYMMDD>_nslab_episode_bundle.md
```

단일 Markdown 안에는 다음 논리 아티팩트를 각각 독립 블록으로 포함한다.

```text
research_report.md
blind_report.md
postmortem_report.md
blind_prediction.json
row_disposition.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
final_semantic_audit.jsonl
market_state_override_audit.jsonl
body_table_candidate_generation_audit.jsonl
ledger_population_audit.json
outcome_leader_census.jsonl
outcome_to_news_audit.jsonl
blind_packet_manifest.json
entity_resolution.jsonl
outcome_ledger.jsonl
research_episode.json
brain_delta.jsonl
source_ledger.jsonl
id_registry.json
validation_report.json
bundle_manifest.json
```

별도의 JSON·JSONL·ZIP·추가 Markdown을 사용자에게 첨부하지 않는다.

내부 임시 파일은 BLIND 봉인과 검증을 위해 반드시 생성한다.

## 2.1 필수 마커

```text
<!-- NSLAB:BEGIN research_report.md -->
<!-- NSLAB:END research_report.md -->

<!-- NSLAB:BEGIN blind_report.md -->
<!-- NSLAB:END blind_report.md -->

<!-- NSLAB:BEGIN postmortem_report.md -->
<!-- NSLAB:END postmortem_report.md -->

<!-- NSLAB:BEGIN blind_prediction.json -->
<!-- NSLAB:END blind_prediction.json -->

<!-- NSLAB:BEGIN row_disposition.jsonl -->
<!-- NSLAB:END row_disposition.jsonl -->

<!-- NSLAB:BEGIN entity_ledger_blind.jsonl -->
<!-- NSLAB:END entity_ledger_blind.jsonl -->

<!-- NSLAB:BEGIN fact_ledger_blind.jsonl -->
<!-- NSLAB:END fact_ledger_blind.jsonl -->

<!-- NSLAB:BEGIN inference_ledger_blind.jsonl -->
<!-- NSLAB:END inference_ledger_blind.jsonl -->

<!-- NSLAB:BEGIN candidate_screening.jsonl -->
<!-- NSLAB:END candidate_screening.jsonl -->

<!-- NSLAB:BEGIN final_semantic_audit.jsonl -->
<!-- NSLAB:END final_semantic_audit.jsonl -->

<!-- NSLAB:BEGIN market_state_override_audit.jsonl -->
<!-- NSLAB:END market_state_override_audit.jsonl -->

<!-- NSLAB:BEGIN body_table_candidate_generation_audit.jsonl -->
<!-- NSLAB:END body_table_candidate_generation_audit.jsonl -->

<!-- NSLAB:BEGIN ledger_population_audit.json -->
<!-- NSLAB:END ledger_population_audit.json -->

<!-- NSLAB:BEGIN outcome_leader_census.jsonl -->
<!-- NSLAB:END outcome_leader_census.jsonl -->

<!-- NSLAB:BEGIN outcome_to_news_audit.jsonl -->
<!-- NSLAB:END outcome_to_news_audit.jsonl -->

<!-- NSLAB:BEGIN blind_packet_manifest.json -->
<!-- NSLAB:END blind_packet_manifest.json -->

<!-- NSLAB:BEGIN entity_resolution.jsonl -->
<!-- NSLAB:END entity_resolution.jsonl -->

<!-- NSLAB:BEGIN outcome_ledger.jsonl -->
<!-- NSLAB:END outcome_ledger.jsonl -->

<!-- NSLAB:BEGIN research_episode.json -->
<!-- NSLAB:END research_episode.json -->

<!-- NSLAB:BEGIN brain_delta.jsonl -->
<!-- NSLAB:END brain_delta.jsonl -->

<!-- NSLAB:BEGIN source_ledger.jsonl -->
<!-- NSLAB:END source_ledger.jsonl -->

<!-- NSLAB:BEGIN id_registry.json -->
<!-- NSLAB:END id_registry.json -->

<!-- NSLAB:BEGIN validation_report.json -->
<!-- NSLAB:END validation_report.json -->

<!-- NSLAB:BEGIN bundle_manifest.json -->
<!-- NSLAB:END bundle_manifest.json -->

<!-- NSLAB:BEGIN direct_ingest_contract.json -->
<!-- NSLAB:END direct_ingest_contract.json -->
```

각 마커는 정확히 한 번만 존재해야 한다.

## 2.2 기계 블록 규칙

```text
JSON은 완전한 유효 JSON
JSONL은 한 줄에 JSON 객체 하나
placeholder·말줄임표·주석 금지
ID와 source_id 일관성 유지
기계 블록에 자유 산문 금지
코드로 실제 파싱 검증
빈 값은 null 또는 빈 배열로 명시
```

원본 뉴스 CSV와 가격 snapshot 전체 본문을 보고서 산문에 복제하지 않는다.

행 ID·입력 SHA-256·snapshot SHA-256으로 추적한다.

────────────────────────────────────────
3. PHASE A — RESEARCH_DAILY SAFE BLIND
────────────────────────────────────────

## 3.1 작업 디렉터리와 phase state

```text
/tmp/nslab_<episode_id>/
├─ phase_state.json
├─ access_log.jsonl
├─ metadata/
│  ├─ research_daily_manifest.json
│  ├─ research_daily_schema.json
│  └─ access_D.json
├─ blind/
│  ├─ blind_snapshot_P.csv
│  ├─ row_disposition.jsonl
│  ├─ entity_ledger_blind.jsonl
│  ├─ fact_ledger_blind.jsonl
│  ├─ inference_ledger_blind.jsonl
│  ├─ candidate_screening.jsonl
│  ├─ blind_prediction.json
│  ├─ blind_report.md
│  ├─ blind_packet_manifest.json
│  └─ blind_seal_receipt.json
├─ postseal_outcome/      # PHASE_3 진입 전 실제 생성 금지
│  ├─ outcome_snapshot_D.csv
│  ├─ entity_resolution.jsonl
│  ├─ outcome_ledger.jsonl
│  ├─ postmortem_report.md
│  └─ outcome_manifest.json
├─ validation/
│  ├─ id_registry.json
│  ├─ phase_audit_report.json
│  ├─ validation_report.json
│  └─ repair_log.jsonl
└─ final/
```

시작 상태:

```text
phase = PHASE_A_RESEARCH_DAILY_SAFE_BLIND
outcome_access_allowed = false
```

`postseal_outcome/` 디렉터리는 위 구조에 경로만 표시할 뿐 PHASE_3 전에는 실제 생성하지 않는다. PHASE_A~PHASE_2에서 `postseal_outcome/` 또는 `outcome_snapshot_D.csv`가 존재하면 즉시 access_log에 기록하고 삭제·quarantine한 뒤, 이번 실행에서 읽었는지 감사한다.

## 3.2 뉴스 CSV 전체 감사

선택된 뉴스 CSV를 GitHub HTML 미리보기가 아니라 Raw URL에서 실제 파일로 다운로드한다.

Python 또는 파일 분석 도구로 전체 행을 파싱한다.

기록:

```text
input_file
input_sha256
input_size_bytes
row_count
columns
valid_row_count
invalid_row_count
min_published_at
max_published_at
time_parse_failure_count
body_missing_count
exact_duplicate_count
semantic_duplicate_cluster_count
rows_outside_expected_window
observed_first_news_at
observed_last_news_at
```

첫 뉴스가 window_start보다 몇 초 늦고 마지막 뉴스가 cutoff보다 몇 초 빠르다는 이유만으로 수집 누락이라 판정하지 않는다.

`input_coverage_warning=true`는 페이지 누락·수집 오류·시간 공백에 대한 적극적 증거가 있을 때만 사용한다.

## 3.3 research_daily 메타데이터 확인

다음 파일을 Raw로 다운로드·파싱한다.

```text
atlas/research_daily/manifest.json
atlas/research_daily/schema.json
access/YYYY/MM/YYYYMMDD.json
```

단, stock-web JSON bytes 저장이 tool/content-type/DNS 제한으로 막히면 JSON을 재구성하지 않는다. web/browser로 routing metadata만 확인하고 `access_sha256_status = WEB_VIEW_ONLY_UNHASHED`로 남긴 뒤, 실제 provenance는 snapshot CSV Raw bytes 검증으로 닫는다.

기록:

```text
research_daily_version
source_atlas_version
source_manifest_sha256
source_commit_hash
price_adjustment_status
research_start_date
max_trade_date
markets
validation_passed
schema_version
schema_columns
access_sha256
```

필수:

```text
manifest.validation_passed == true
D <= manifest.max_trade_date
access.build_status == complete
schema.columns가 snapshot 실제 header와 일치
```

## 3.4 BLIND snapshot만 다운로드·검증

access의 `blind_snapshot_path`를 `repository_raw_base_url`에 결합해 Raw 파일을 다운로드한다.

이 단계에서는 `outcome_snapshot_path`를 다운로드하지 않는다.

BLIND snapshot 검증:

```text
실제 SHA-256 == access.blind_snapshot_sha256
실제 행 수 == access.blind_snapshot_row_count
고유 code 수 == 행 수
모든 snapshot_date == access.blind_snapshot_date == P
모든 max_source_date <= P
모든 previous_market_trade_date <= P
code는 선행 0을 보존한 6자리 문자열
market은 KOSPI|KOSDAQ|KOSDAQ GLOBAL
header == schema.columns
```

검증 실패 시 BLIND snapshot을 사용하지 않는다.

```text
blind_market_context_status = INVALID_RESEARCH_DAILY_BLIND_SNAPSHOT
```

으로 기록하고 뉴스-only BLIND를 계속하되 outcome은 PENDING 처리한다.

검증 성공 시:

```text
blind_context_mode = NEWS_PLUS_RESEARCH_DAILY_P_SNAPSHOT
safe_d1_packet_only = true
max_exposed_price_date = P
no_D_outcome_exposed = true
```

BLIND 중 다음 경로는 열지 않는다.

```text
outcome_snapshot_path
symbol_profiles
all_symbols
current_symbols
latest fields
종목별 연도 shard
포털 시세
```

## 3.5 모든 뉴스 행 전수 분류

모든 유효 행에 고유 ID를 부여한다.

```text
NEWS-000001
NEWS-000002
...
```

모든 유효 행은 `row_disposition.jsonl`에 정확히 한 번 등장해야 한다.

허용 primary disposition:

```text
KR_CORPORATE_EVENT_CANDIDATE
OTHER_CORPORATE_EVENT
THEME_POLICY_INDUSTRY_EVENT
MACRO_GEOPOLITICAL_EVENT
MARKET_STATE_CONTINUATION_SIGNAL
DISCLOSURE_OR_MARKET_NOTICE
DUPLICATE
NON_PRICE_RELEVANT
UNRESOLVED_REQUIRES_REVIEW
```

각 레코드:

```json
{
  "row_id": "NEWS-000001",
  "published_at": "",
  "primary_disposition": "",
  "event_ids": [],
  "entity_ids": [],
  "duplicate_of_row_id": null,
  "price_relevance": "high | medium | low | none | unresolved",
  "review_reason": "",
  "review_passes": [],
  "confidence_label": "high | medium | low"
}
```

### 3.5.1 행 분류 4패스

```text
PASS 1 — 구조 파싱
날짜·시간·명시적 6자리 코드·공시 문구·중복 후보만 추출
임의 명사구를 회사로 추출하지 않음

PASS 2 — 의미 분류
최대 100행 단위로 모든 행을 순서대로 읽음

PASS 3 — 역감사
NON_PRICE_RELEVANT·THEME·MACRO·MARKET_STATE 행 속 구체 회사행위를 재검토

PASS 4 — 교차 커버리지
모든 명시적 6자리 코드와 승인된 회사 entity가 disposition·event·명시적 제외 중 하나에 연결됐는지 확인
```

`UNRESOLVED_REQUIRES_REVIEW`는 봉인 전에 다시 검토한다.

### 3.5.2 행 커버리지 게이트

```text
disposition_record_count == valid_row_count
unique_disposition_row_count == valid_row_count
unassigned_row_count == 0
duplicate_disposition_row_count == 0
invalid_row_reference_count == 0
```

실패하면 최대 3회 복구한다.

## 3.6 Issuer Entity Gate

직접 기업뉴스 엔티티는 세 단계로 검증한다.

### 3.6.1 E1 Extractor

각 proposed entity는 다음을 가져야 한다.

```text
entity_literal
원문 속 exact span
char_start
char_end
entity_role
corporate_predicate
predicate_char_start
predicate_char_end
entity_type_candidate
ticker_literal_or_null
ticker_binding_span_or_null
```

허용 entity type candidate:

```text
KR_LISTED_ISSUER_CANDIDATE
KR_UNLISTED_COMPANY
FOREIGN_COMPANY
GROUP_OR_BRAND
GOVERNMENT_OR_PUBLIC_BODY
PERSON
SPORTS_OR_ENTERTAINMENT_ENTITY
PRODUCT_OR_SERVICE
PLACE_OR_VENUE
GENERIC_PHRASE
UNKNOWN
```

### 3.6.2 E2 Independent Verifier

판정:

```text
ACCEPT_KR_ISSUER_CANDIDATE
ACCEPT_OTHER_CORPORATE_CONTEXT
REJECT_PERSON
REJECT_SPORTS_OR_ENTERTAINMENT
REJECT_GOVERNMENT_OR_PUBLIC_BODY
REJECT_PLACE_OR_REGION
REJECT_PRODUCT_OR_BRAND_ONLY
REJECT_GENERIC_OR_HEADLINE_FRAGMENT
REJECT_FOREIGN_OR_NON_KR_LISTED_CONTEXT
AMBIGUOUS_NEEDS_ADJUDICATION
```

### 3.6.3 E3 Adjudicator

Extractor·Verifier 불일치 또는 low confidence는 제3 패스로 판정한다.

새 엔티티를 만들지 않고 승인·거절·보류만 한다.

### 3.6.4 STRICT whole-entity issuer binding

`entity mention`과 `listed issuer binding`을 분리한다.

원문에 회사처럼 보이는 문자열이 있다는 사실만으로 ticker를 부여하지 않는다.

BLIND ticker binding은 아래 세 경우에만 허용한다.

```text
A. EXPLICIT_LOCAL_TICKER
   - 정확한 6자리 코드가 entity와 같은 문장·괄호·대괄호 안에 존재
   - entity span과 ticker span의 소유관계가 문법적으로 명확
   - P snapshot에 code가 있으면 날짜 시점 name까지 교차검증
   - P snapshot에 code가 없더라도 CSV의 명시적 ticker 자체는 삭제하지 않음
   - 이 경우 `EXPLICIT_TICKER_NOT_IN_P_SNAPSHOT`으로 보존하고 상장상태는 post-seal에서 확정

B. EXACT_WHOLE_NAME_P_SNAPSHOT
   - entity literal 전체를 제한적으로 정규화한 값
     == blind snapshot(P)의 회사명 전체를 같은 방식으로 정규화한 값
   - unique match

C. EXACT_CLEAN_ALIAS_MEMORY
   - available_from <= D인 이전 clean company alias memory
   - alias 전체 exact match
   - unique match
```

허용 정규화는 다음뿐이다.

```text
Unicode NFKC
앞뒤 공백 제거
연속 공백 1개로 축소
법인표기 (주), ㈜, 주식회사를 독립 토큰일 때만 제거
동일 의미의 괄호·중점·전각기호 정규화
```

다음을 절대 제거하거나 축약하지 않는다.

```text
그룹
플랫폼
볼파크
재단
대학교
연구원
서비스
전자
바이오
테크
홀딩스
기타 의미를 바꾸는 모든 문자·토큰
```

금지 binding:

```text
prefix match
suffix match
substring match
fuzzy match
embedding 유사도 match
편집거리 match
기사 수준 코드 전파
그룹명에서 임의 계열사 선택
브랜드·제품·서비스에서 임의 상장사 선택
장소·경기장·행사명에서 포함된 회사명을 추출해 issuer 처리
복수 snapshot 후보 중 임의 선택
현재 latest 회사명 사용
```

### 3.6.4.1 문자 경계 규칙

명시적 ticker가 없는 이름 binding은 entity span이 원문에서 독립된 전체 표현이어야 한다.

entity span 바로 앞·뒤가 한글·영문·숫자로 이어지는 더 긴 token이면 substring으로 간주하고 binding을 거절한다.

단, exact 회사명 바로 뒤에 붙는 아래 한국어 조사·조직 표지만 제한적으로 허용한다.

```text
은 는 이 가 을 를 의 과 와 도 에 에서 으로 로 만 측
```

조사 허용은 entity span 밖의 접미 형태에만 적용하며, 회사명 자체 정규화에는 사용하지 않는다.

overlap이 있는 entity 후보는 **가장 긴 원문 span을 먼저 분류**한다.

긴 span이 GROUP_OR_BRAND·PLACE_OR_VENUE·PRODUCT_OR_SERVICE·GENERIC_PHRASE이면 그 내부의 짧은 회사명 substring을 별도 issuer로 되살리지 않는다.

### 3.6.4.2 알려진 실패형 regression fixture

실제 입력 분석 전에 아래 synthetic fixture를 코드로 실행한다.

```text
"삼성전자[005930]가 공급한다"
→ 삼성전자 / 005930 / EXPLICIT_LOCAL_TICKER / ACCEPT

"에스엘플랫폼이 사명을 변경했다"
blind snapshot에 에스엘만 존재
→ 에스엘로 binding 금지

"현대차그룹이 로봇 투자를 검토한다"
blind snapshot에 현대차 존재
→ 현대차로 binding 금지, GROUP_OR_BRAND

"한화생명볼파크에서 경기가 열렸다"
blind snapshot에 한화생명 존재
→ 한화생명으로 binding 금지, PLACE_OR_VENUE

"디바이스 수요가 증가했다"
→ 어떤 상장사에도 binding 금지, GENERIC_PHRASE
```

필수 결과:

```text
entity_binding_regression_test_count >= 5
entity_binding_regression_failure_count == 0
```

실패하면 실제 BLIND entity extraction을 시작하지 않고 binding 코드를 수리한 뒤 fixture를 다시 실행한다.

### 3.6.4.3 unresolved 보존

정확한 binding이 불가능하면 mention 자체를 삭제하지 않는다.

```text
blind_entity_status = AMBIGUOUS 또는 OTHER_CONTEXT
resolved_ticker_from_P_snapshot_or_null = null
binding_status = UNRESOLVED_NO_EXACT_BINDING
```

명시적 ticker조차 없는 unresolved mention은 사건·테마 문맥에는 사용할 수 있지만, BLIND 직접 상장사 후보·issuer-day 학습표본으로는 사용하지 않는다.

CSV에 entity-local 6자리 ticker가 명시됐으나 P snapshot에 행이 없는 경우에는 다음처럼 별도 보존한다.

```text
blind_entity_status = EXPLICIT_TICKER_UNRESOLVED_LISTING
ticker_literal_or_null = 원문 ticker
resolved_ticker_from_P_snapshot_or_null = null
binding_status = EXPLICIT_TICKER_PENDING_POSTSEAL_LISTING_CHECK
```

이 경우 BLIND 후보 심사는 가능하지만, post-seal에서 실제 한국 상장사와 날짜 시점 동일성이 확인되기 전에는 감독학습 record를 적격 처리하지 않는다.

POSTMORTEM에서 공식 alias·공시·D snapshot으로 해결할 수 있으나, 그 결과를 BLIND에서 ticker를 알고 있었던 것처럼 소급하지 않는다.

### 3.6.5 blind entity ledger

```json
{
  "entity_id": "ENT-000001",
  "row_ids": ["NEWS-000001"],
  "entity_literal": "",
  "entity_span": {"start": 0, "end": 0},
  "entity_role": "",
  "corporate_predicate": "",
  "predicate_span": {"start": 0, "end": 0},
  "extractor_type": "",
  "verifier_decision": "",
  "adjudicator_decision": null,
  "blind_entity_status": "KR_ISSUER_CANDIDATE | OTHER_CONTEXT | REJECTED | AMBIGUOUS",
  "ticker_literal_or_null": null,
  "resolved_ticker_from_P_snapshot_or_null": null,
  "resolved_name_on_P_or_null": null,
  "ticker_binding_method": "EXPLICIT_LOCAL_TICKER | EXACT_WHOLE_NAME_P_SNAPSHOT | EXACT_CLEAN_ALIAS_MEMORY | null",
  "ticker_binding_evidence": null,
  "binding_status": "BOUND_EXACT | UNRESOLVED_NO_EXACT_BINDING | NOT_APPLICABLE",
  "whole_entity_match": false,
  "token_boundary_valid": false,
  "longer_phrase_collision": false,
  "substring_match_rejected": false,
  "normalization_operations": [],
  "rejection_reason": null,
  "confidence_label": "high | medium | low"
}
```

### 3.6.6 엔티티 필수 게이트

```text
issuer_candidate_without_predicate_count == 0
issuer_candidate_not_literal_in_source_count == 0
article_level_ticker_propagation_count == 0
accepted_noncorporate_as_issuer_count == 0
prefix_binding_count == 0
suffix_binding_count == 0
substring_binding_count == 0
fuzzy_binding_count == 0
group_to_member_binding_count == 0
brand_or_product_to_issuer_binding_count == 0
place_or_venue_to_issuer_binding_count == 0
generic_phrase_to_issuer_binding_count == 0
entity_binding_regression_failure_count == 0
accepted_binding_without_whole_entity_or_local_ticker_count == 0
```

이 게이트는 BLIND seal 전에 실제 ledger를 전수 순회해 계산한다.


## 3.6.7 BLIND Atomic Fact·Inference Ledger

엔티티 게이트를 통과한 행은 observation을 만들기 전에 원자 사실을 추출한다.

### Fact 추출

각 사실은 입력 row의 exact quote와 offset을 가진다.

```text
fact_id
row_id
entity_id_or_null
subject_literal
predicate_statement
object_or_value
qualifiers
temporal_expression
modality
exact_quote
quote_char_start
quote_char_end
source_ids
extractor_confidence
verifier_decision
verifier_reason
```

독립 verifier가 `ENTAILED`로 판정하지 않은 사실은 후보 feature와 Brain Delta에 사용하지 않는다.

### Inference 추출

원문 밖의 경제적 해석은 별도 inference로 저장한다.

```text
inference_id
statement
inference_type
supporting_fact_ids
scope_event_ids
scope_entity_ids
uncertainty
verifier_decision
verifier_reason
```

사실과 추론을 한 필드에 섞지 않는다.

### 누수 검사

```text
fact row가 event input row에 속함
fact entity가 observation entity와 일치
다른 회사의 고객·제품·계약을 가져오지 않음
다른 event feature를 복사하지 않음
분석가 전망을 확정 사실로 바꾸지 않음
```

최종 candidate·screening·event·theme는 fact/inference ID를 참조한다.

## 3.7 직접 기업뉴스 관측 장부

Issuer Entity Gate를 통과한 모든 엔티티에 observation을 만든다.

```text
observation_id
entity_id
input_row_ids
published_at
company_name_literal
ticker_literal_or_null
resolved_ticker_from_P_or_null
event_id
fact_ids
inference_ids
event_summary
news_type_open_text
unknowns
preliminary_relevance
observation_status
```

`event_summary`는 fact를 압축한 완전한 문장이어야 하며, fact에 없는 내용을 추가하지 않는다.

`observation_status`:

```text
DIRECT_EVENT_INCLUDED_FOR_SCREENING
DIRECT_EVENT_DUPLICATE
ENTITY_UNRESOLVED_AT_BLIND
```

애널리스트·시장전망 기사도 issuer-specific 내용이 있으면 observation을 만든다. 다만 전망 문장은 `modality=ANALYST_VIEW` fact로 남기고 회사 확정 사실로 승격하지 않는다.

## 3.8 모든 observation 후보 심사

각 고유 event-company observation은 `candidate_screening.jsonl`에 정확히 한 번 등장해야 한다.

P snapshot에서 ticker가 확정된 경우 다음 D-1 특징을 붙인다.

```text
P_close
P_market_cap
P_listed_shares
P_amount
P_turnover_pct
P_amount_rank
P_turnover_rank
P_market_cap_rank
P_return_3d_pct
P_return_5d_pct
P_return_10d_pct
P_return_20d_pct
P_upper_limit_touch_count_5d
P_upper_limit_close_count_5d
P_high_return_ge_10_count_5d
P_high_return_ge_20_count_5d
P_corporate_action_warning
P_data_quality_status
```

이 특징을 고정 점수표로 변환하지 않는다.

각 screening:

```json
{
  "screening_id": "SCR-000001",
  "observation_id": "OBS-000001",
  "event_id": "EVT-000001",
  "entity_id": "ENT-000001",
  "company_name_literal": "",
  "ticker_or_null": null,
  "supporting_fact_ids": [],
  "supporting_inference_ids": [],
  "semantic_feature_summary": [
    {
      "feature_id": "FEAT-000001",
      "statement": "",
      "feature_kind": "EXTRACTED_FACT | BLIND_INFERENCE",
      "supporting_fact_ids": [],
      "supporting_inference_ids": [],
      "verifier_status": "ENTAILED | SUPPORTED | INVALID"
    }
  ],
  "safe_D1_features": {},
  "candidate_decision": "INCLUDE | EXCLUDE | WATCH_SECONDARY | UNRESOLVED",
  "decision_reason": "",
  "preliminary_priority": "very_high | high | medium | low | none",
  "eligible_for_final_ranking": true,
  "source_ids": []
}
```

금지:

```text
근거 없는 AI_COMMERCIALIZATION·GLOBAL_CUSTOMER 같은 태그
다른 event의 feature 복사
분석가 의견을 confirmed fact로 저장
완성되지 않은 영어 템플릿 문장
```

필수:

```text
screening_record_count == unique_direct_observation_count
unscreened_direct_observation_count == 0
high_medium_observation_without_decision_count == 0
INVALID semantic_feature count == 0 for eligible records
```

출처 유형 자체를 자동 벌점으로 사용하지 않는다.

## 3.9 사건 군집화

같은 원인 사건의 반복기사를 하나의 event로 묶는다.

허용 enum:

```text
scope = single_company | theme | macro | mixed
novelty = new | follow_up | recycled | unclear
certainty = confirmed | announced | under_review | speculative | unclear
```

각 event:

```text
event_id
event_title
event_summary
scope
first_published_at
last_published_at_before_cutoff
input_row_ids
source_ids
direct_entity_ids
direct_company_literals
direct_ticker_literals
novelty
certainty
authority_from_csv
fact_ids
inference_ids
confirmed_facts_from_csv_summary
causal_mechanisms_as_inference_ids
open_questions
contrary_evidence_fact_or_inference_ids
```

## 3.10 오픈월드 최초 분석

현재 CSV와 P snapshot만 보고 다음을 도출한다.

```text
직접 기업 사건
정책·산업·지역 사건
거시·지정학 사건
사건 간 결합 가능성
경제적 수혜 층
시장 내러티브 층
상승·하락 양방향 시나리오
향후 조사할 질문
```

거시 사건은 최소 다음 세 상태를 검토한다.

```text
escalation
base
relief_or_deescalation
```

## 3.11 BLIND 후보 생성

### A. SINGLE_EVENT

`candidate_screening`에서 INCLUDE 또는 WATCH_SECONDARY인 직접 observation을 비교한다.

### B. THEME_FORMATION

각 정책·산업·지역·글로벌 사건에 다음을 작성한다.

```text
formation_mechanism
direct_benefit_layer
indirect_benefit_layer
market_narrative_layer
candidate_archetypes
sealed_peer_universe
peer_membership_provenance
failure_conditions
```

### C. THEME_BENEFICIARY

정확한 종목 후보는 다음 근거가 있을 때만 넣는다.

```text
CSV 직접 등장
available_from <= D인 과거 clean relation memory
현재 입력 뉴스 안의 명시적 공급망·고객·지역 관계
```

P snapshot은 상장 여부·시총·수급 확인용이지 사업 관계 사전이 아니다.

사업 관계 근거가 없으면 종목을 억지 생성하지 않고 archetype으로 남긴다.

### D. CONTINUATION

P snapshot을 사용해 다음을 검토한다.

```text
전일 상한가 터치·마감
최근 5일 상한가 횟수
최근 5일 +10%/+20% 횟수
P 거래대금·회전율 순위
3·5·10·20일 수익률
현재 뉴스와의 사건·테마 연결
```

단순히 전일 급등했다는 이유만으로 후보에 넣지 않는다.

현재 뉴스 또는 clean market memory와 연결되는 경우에만 continuation 후보로 생성한다.

## 3.12 후보별 BLIND 장부

```text
candidate_id
company_name
ticker_or_null
path_type
event_ids
observation_ids
screening_ids
directly_mentioned
preopen_thesis
why_now
causal_chain
supporting_fact_ids
supporting_inference_ids
blind_used_evidence_summary
safe_D1_features
past_clean_memory_evidence
model_inference_unverified
counterarguments
disconfirming_conditions
confidence_label
evidence_quality
source_ids
```

## 3.13 BLIND pairwise 비교

BLIND에 같은 테마의 구체 종목 후보가 둘 이상 있을 때만 비교한다.

이 비교는 봉인 후 leader training의 유일한 정식 모집단이다.

```text
pair_id
preferred_candidate_id
rejected_candidate_id
theme_id
blind_available_features
safe_D1_features
blind_preference_reason
```

## 3.14 BLIND Red-team

다음을 검토한다.

```text
좋은 기업뉴스일 뿐 상한가형이 아닌가
신규 사실이 아닌가
전체 사업비를 회사 귀속액으로 오인했는가
MOU·협의·예정·프로토타입인가
희석·오버행이 있는가
관련주 연결이 억지인가
거시 사건 반대 방향을 놓쳤는가
직접 소형주 기사를 broad theme 속에서 누락했는가
issuer-specific 리포트의 구체 제품·점유율·실적 bridge를 놓쳤는가
P snapshot의 최근 급등·상한가를 선반영 위험과 연속성 양쪽으로 검토했는가
```

## 3.15 BLIND 최종 목록

```text
row_disposition_summary
entity_quality_summary
fact_quality_summary
inference_quality_summary
candidate_screening_summary
research_daily_blind_snapshot_summary
all_direct_event_observations
event_clusters
open_world_first_read
dominant_sector_hypotheses
theme_candidate_archetypes
theme_beneficiary_candidates
single_event_candidates
continuation_candidates
blind_pairwise_preferences
final_watchlist
excluded_but_notable
blind_limitations
```

최종 watchlist는 최대 20개지만 직접 observation과 screening 모집단에는 개수 제한을 두지 않는다.

이 제한은 권고가 아니라 hard gate다.

```text
final_watchlist_size <= 20
max(final_watchlist.rank) <= 20
rank_set == 1..N
duplicate_ticker_count == 0
```

rank 21 이상은 final_watchlist에 존재할 수 없다. 21번째 이후 후보는 `candidate_screening_population`, `secondary_watch_pool`, `continuation_watchlist`, `excluded_but_notable` 중 하나로 이동해야 한다.

최종 rank는 1부터 N까지 빈칸 없이 연속돼야 한다. 후보 삭제 후 순위를 다시 매겨야 하며 10위·19위 같은 누락을 허용하지 않는다.

validator는 `expected_rank_set`을 생성 결과의 `max_rank`에서 만들 수 없다. 반드시 `final_watchlist_size <= 20`을 먼저 확인하고, 그 뒤 `expected_rank_set = 1..final_watchlist_size`로 검증한다.

최종 후보의 `reason`은 소형시총·거래회전·최근 고가 이력만으로 구성될 수 없다. 각 final item은 최소 1개 이상의 `source_fact_ids`와 원문 quote에 연결된 concrete catalyst를 가져야 한다.

## 3.16 BLIND 품질 게이트

공통 필수 게이트:

```text
csv_full_parse_complete == true
blind_web_search_call_count == 0
blind_current_price_access_count == 0
blind_outcome_snapshot_download_count == 0
no_D_outcome_exposed == true
row_disposition_coverage_ratio == 1.0
silent_direct_event_omission_count == 0
unscreened_direct_observation_count == 0
issuer_candidate_without_predicate_count == 0
issuer_candidate_not_literal_in_source_count == 0
article_level_ticker_propagation_count == 0
accepted_noncorporate_as_issuer_count == 0
fact_quote_not_found_count == 0
fact_offset_mismatch_count == 0
training_fact_not_entailed_count == 0
training_inference_unsupported_count == 0
cross_event_feature_leak_count == 0
cross_issuer_feature_leak_count == 0
feature_without_reference_count == 0
```

정상 research_daily 모드의 추가 필수 게이트:

```text
research_daily_access_valid == true
blind_snapshot_hash_verified == true
blind_snapshot_row_count_verified == true
blind_snapshot_dates_safe == true
```

공식 거래일인데 research_daily package가 없거나 blind snapshot 검증이 끝내 실패한 경우에는 뉴스-only BLIND를 봉인할 수 있다.

그 경우 다음을 명시한다.

```text
blind_context_mode = NEWS_ONLY_FALLBACK
research_daily_access_valid = false 또는 not_available
continuation_analysis_status = UNAVAILABLE
forecast_evaluation_eligible = false
bundle_status = PENDING_OUTCOME
```

이 fallback은 정상 research_daily 데이터가 존재하는데 다운로드 노력을 생략하기 위한 경로가 아니다.

## 3.17 BLIND 패킷 물리적 봉인

다음 파일을 먼저 저장한다.

```text
blind_prediction.json
blind_report.md
row_disposition.jsonl
entity_ledger_blind.jsonl
fact_ledger_blind.jsonl
inference_ledger_blind.jsonl
candidate_screening.jsonl
```

각 파일의 SHA-256을 계산해 `blind_packet_manifest.json`에 기록한다.

`blind_packet_manifest.json`에는 다음 가격 접근 감사도 포함한다.

```text
access_url
access_sha256
blind_snapshot_path
blind_snapshot_sha256_expected
blind_snapshot_sha256_actual
blind_snapshot_row_count_expected
blind_snapshot_row_count_actual
blind_snapshot_date
blind_max_source_date
outcome_snapshot_path_not_downloaded = true
```

`blind_packet_manifest.json` 자체의 canonical SHA-256도 계산한다.

그 후:

```text
모든 BLIND 파일 재읽기
모든 해시 재검증
phase_state = BLIND_SEALED
가능하면 읽기 전용 처리
```

봉인 성공 전에는 outcome snapshot·웹·POSTMORTEM을 시작하지 않는다.

────────────────────────────────────────
4. PHASE B — POST-SEAL FULL-MARKET OUTCOME
────────────────────────────────────────

## 4.1 seal 재검증

D 결과 접근 직전에 다음을 코드로 확인한다.

```text
phase_state == BLIND_SEALED
모든 BLIND 파일 hash == blind_packet_manifest
blind_packet_manifest hash == seal receipt
outcome_snapshot_path_not_downloaded == true
```

실패하면 outcome snapshot을 열지 않는다.

## 4.2 OUTCOME snapshot 다운로드·완전성 검증

seal 검증 뒤 access의 `outcome_snapshot_path`를 Raw로 다운로드한다. 이때 처음으로 `postseal_outcome/` 디렉터리를 만들 수 있다. `blind_snapshot_P.csv`와 `outcome_snapshot_D.csv`를 같은 루프에서 함께 처리하지 않는다.

검증:

```text
실제 SHA-256 == access.outcome_snapshot_sha256
실제 행 수 == access.outcome_snapshot_row_count
고유 code 수 == 행 수
모든 snapshot_date == access.outcome_snapshot_date == D
모든 max_source_date <= D
header == schema.columns
market은 KOSPI|KOSDAQ|KOSDAQ GLOBAL
```

검증 실패 시 최대 3회 다시 다운로드한다.

계속 실패하면:

```text
status = COMPLETED_BLIND_PENDING_OUTCOME
outcome_status = RESEARCH_DAILY_OUTCOME_VALIDATION_FAILED
```

로 기록한다.

기존 종목 shard·포털 TOP30·기사 목록으로 부분 outcome을 대신 만들지 않는다.

검증 성공 시:

```text
outcome_coverage_status = FULL_MARKET_RESEARCH_DAILY
full_market_complete = true
upper_limit_census_complete = true
```

## 4.3 Post-seal Entity Resolution

BLIND ledger는 수정하지 않는다.

별도의 `entity_resolution.jsonl`을 만든다.

검증 우선순위:

```text
1. entity-local 명시적 6자리 코드
2. blind snapshot(P)의 날짜 시점 code-name
3. outcome snapshot(D)의 날짜 시점 code-name
4. cutoff 이전 공시·회사 공식자료
5. 신뢰도 높은 언론
```

현재 latest universe를 사용하지 않는다.

각 accepted issuer candidate를 다음으로 확정한다.

```text
KR_LISTED_ON_P_AND_D
KR_LISTED_ON_P_NO_TRADABLE_ROW_D
KR_NEWLY_PRESENT_ON_D
KR_NOT_LISTED_ON_D
KR_UNLISTED_COMPANY
FOREIGN_COMPANY
GROUP_OR_BRAND_WITHOUT_LISTED_ISSUER
PUBLIC_BODY
PERSON_OR_NONCORPORATE
AMBIGUOUS_UNRESOLVED
```

각 resolution:

```json
{
  "entity_id": "ENT-000001",
  "blind_entity_literal": "",
  "resolved_ticker": null,
  "resolved_company_name_on_P": null,
  "resolved_company_name_on_D": null,
  "listing_status": "",
  "resolution_method": "",
  "entity_local_binding_verified": true,
  "alternative_candidates": [],
  "resolution_confidence": "high | medium | low",
  "false_positive_found_postseal": false,
  "resolution_source_ids": []
}
```

필수:

```text
article_level_ticker_propagation_postseal_count == 0
resolved_ticker_collision_without_explanation_count == 0
```

모든 BLIND `BOUND_EXACT` entity를 post-seal에서 독립적으로 100% 재검증한다.

검증기는 BLIND의 `ticker_binding_evidence` 결론을 그대로 믿지 않고 다음 원자료를 다시 읽는다.

```text
원문 entity span과 주변 문맥
명시적 local ticker span
P snapshot code-name
D snapshot code-name
available_from <= D인 clean alias memory
cutoff 이전 공식 공시·회사자료
```

각 binding에 다음을 계산한다.

```text
whole_entity_match_reverified
local_ticker_ownership_reverified
token_boundary_reverified
entity_type_reverified
prefix_or_substring_collision_found
group_brand_venue_product_collision_found
```

`false_positive_found_postseal=true`인 entity는 BLIND 파일을 고쳐 쓰지 않는다.

대신:

```text
해당 direct event·issuer-day·leader pair training_eligible = false
entity_resolution_error_case 생성
ACCEPT_FULL 금지
```

최종 `ACCEPT_FULL`에는 다음이 필수다.

```text
accepted_issuer_false_positive_count == 0
postseal_unresolved_training_issuer_count == 0
training_eligible_entity_binding_error_count == 0
```

## 4.4 outcome_ledger.jsonl — 전 시장 전체

outcome snapshot의 모든 행을 compact record로 변환한다.

행 수는 access의 outcome row count와 같아야 한다.

각 레코드:

```json
{
  "ticker": "000000",
  "company_name_on_D": "",
  "market": "",
  "snapshot_date": "",
  "previous_market_trade_date": "",
  "prev_symbol_trade_date": "",
  "prev_close": null,
  "open": null,
  "high": null,
  "low": null,
  "close": null,
  "volume": null,
  "amount": null,
  "market_cap": null,
  "listed_shares": null,
  "open_gap_pct": null,
  "high_return_pct": null,
  "low_return_pct": null,
  "close_return_pct": null,
  "turnover_pct": null,
  "amount_rank": null,
  "turnover_rank": null,
  "market_cap_rank": null,
  "high_return_rank": null,
  "close_return_rank": null,
  "limit_up_price": null,
  "upper_limit_touched": null,
  "upper_limit_closed": null,
  "upper_limit_released": null,
  "one_price_upper_limit": null,
  "upper_limit_label_status": "",
  "corporate_action_warning": false,
  "new_listing_or_no_reference": false,
  "data_quality_status": "",
  "label_quality": "verified | quarantined"
}
```

`label_quality=verified` 조건:

```text
upper_limit_label_status == verified_normal_day
data_quality_status == clean 또는 usable_with_caveat
corporate_action_warning == false
new_listing_or_no_reference == false
```

그 외에는 `quarantined`다.

## 4.5 전 시장 실제 승자 census

outcome_ledger 전체에서 다음 집합을 전수 생성한다.

```text
verified upper_limit_touched
verified upper_limit_closed
verified upper_limit_released
verified one_price_upper_limit
verified high_return_pct >= 20
verified high_return_pct >= 15
verified high_return_pct >= 10
verified high_return_pct >= 5
amount_rank 상위군
turnover_rank 상위군
```

차단 행은 별도 quarantine census로 분리한다.

상한가 목록은 snapshot 필드로 확정하며 외부 기사·포털 목록으로 대체하지 않는다.

## 4.6 모든 직접뉴스·BLIND 후보 outcome 결합

Post-seal resolution된 ticker를 outcome ledger와 조인한다.

상태:

```text
EXACT_D_TRADABLE_ROW
NO_TRADABLE_ROW_ON_D
QUARANTINED_PRICE_LABEL
UNRESOLVED_ENTITY
NOT_KR_LISTED
```

모든 직접 event-company observation과 모든 BLIND 후보에 상태를 부여한다.

```text
outcome_target_count
outcome_join_attempt_count
exact_D_row_count
no_D_row_count
quarantined_count
unresolved_count
```

필수:

```text
outcome_join_attempt_count == outcome_target_count
```

## 4.7 issuer-day 집계

같은 `trade_date + ticker`의 모든 직접 event를 하나의 issuer-day로 묶는다.

```json
{
  "issuer_day_case_id": "20260622:005930",
  "trade_date": "2026-06-22",
  "ticker": "005930",
  "company_name_on_D": "",
  "event_ids": [],
  "observation_ids": [],
  "screening_ids": [],
  "combined_fact_ids": [],
  "combined_inference_ids": [],
  "event_count": 0,
  "event_level_sample_weight": 0.0,
  "attribution_status": "SINGLE_EVENT | MULTI_EVENT_ATTRIBUTION_AMBIGUOUS | DOMINANT_EVENT_WITH_EVIDENCE",
  "safe_D1_features": {},
  "D_outcome": {},
  "response_class": "",
  "label_quality": "verified | quarantined | no_tradable_row",
  "training_eligible": true
}
```

```text
event_level_sample_weight = 1 / event_count
```

한 issuer-day의 event-level weight 합은 1이어야 한다.

가격 결과 통계와 모델 평가의 기본 단위는 issuer-day다.

## 4.8 POSTMORTEM 웹조사

BLIND 봉인 후에는 웹 조사 가능하다.

각 실제 승자·주요 오탐·누락 후보에 대해 다음을 조사한다.

```text
최초 촉매 공개시각
cutoff 이전 정보 존재 여부
직접 회사뉴스 여부
정책·산업 테마 여부
장중 신규 뉴스 여부
전일 시장기억 여부
뉴스 없는 수급 가능성
```

출처 우선순위:

```text
DART·KIND·KRX
정부·지자체·공공기관
회사 IR·공식 보도자료
계약 상대방 공식자료
신뢰도 높은 언론
과거 시장기억 확인용 기사
```

게시시각에 따라 다음을 분리한다.

```text
published_at <= cutoff
→ cutoff-available evidence

cutoff < published_at <= D 장중
→ TIMING_IMPOSSIBLE 장중 촉매

published_at > D
→ 사후 설명 전용, 장전 증거 사용 금지
```

사후 기사 문구를 원인으로 그대로 믿지 않고 최초 공개시각과 원자료를 확인한다.

────────────────────────────────────────
5. PHASE C — 모집단 기반 Supervised Postmortem
────────────────────────────────────────

## 5.1 직접뉴스 event record와 issuer-day record를 분리한다

### 5.1.1 event-company case

모든 post-seal KR 상장사 event-company observation을 보존한다.

각 case:

```text
case_id
event_id
entity_id
observation_id
screening_id
issuer_day_case_id
ticker
company_name
blind_observed
blind_candidate
blind_rank
candidate_decision
blind_fact_ids
blind_inference_ids
safe_D1_features
D_outcome
response_class
label_quality
sample_weight
training_eligible
failure_or_success_notes
```

이 record는 뉴스 메커니즘 학습용이다.

가격 결과를 독립 표본처럼 중복 집계하지 않는다.

### 5.1.2 issuer-day supervised case

가격 반응 학습·통계·평가의 기본 단위다.

```text
issuer_day_case_id
trade_date
ticker
all_event_ids
combined_fact_ids
combined_inference_ids
safe_D1_features
D_outcome
response_class
attribution_status
training_eligible
```

## 5.2 response class

```text
positive_upper_limit_close
positive_upper_limit_touch
positive_high20
positive_high15
positive_high10
near_miss_high5
neutral
negative
no_tradable_row
unresolved_outcome
corporate_action_quarantine
```

상한가 사례만 만들지 않는다.

positive·negative·near-miss를 모두 보존한다.

## 5.3 후보 생성·순위·사건 논지 오류를 분리한다

허용 오류 유형:

```text
ROW_CLASSIFICATION_MISS
ENTITY_MISSING
ENTITY_FALSE_POSITIVE
TICKER_BINDING_ERROR
EVENT_CLUSTER_ERROR
CANDIDATE_SCREENING_MISS
CANDIDATE_GENERATION_MISS
RANKING_MISS
EVENT_THESIS_SELECTION_MISS
EVENT_ATTRIBUTION_MISS
MARKET_STATE_OR_CONTINUATION_CASE
CAPITAL_ACTION_RESPONSE_CASE
NEWSLESS_OR_UNEXPLAINED
```

오류를 만들기 전에 ticker 기준과 event 기준을 모두 대조한다.

```text
1. 실제 강한 상승 ticker가 BLIND observation에 있었는가
2. screening에 있었는가
3. concrete candidate에 있었는가
4. final watchlist에 같은 ticker가 어떤 event로라도 있었는가
5. 실제 반응과 더 가까운 event가 별도로 있었는가
6. 같은 issuer-day에 복수 event가 있었는가
```

판정 규칙:

```text
같은 ticker가 final watchlist에 없음 + concrete candidate에도 없음
→ 단계에 따라 SCREENING_MISS 또는 GENERATION_MISS

같은 concrete candidate가 있었으나 final watchlist에서 빠짐/과도하게 낮음
→ RANKING_MISS

같은 ticker가 final watchlist에 이미 있으나 다른 event를 대표 논지로 선택
→ EVENT_THESIS_SELECTION_MISS

복수 event 중 인과 귀속을 잘못 단정하거나 선택하지 못함
→ EVENT_ATTRIBUTION_MISS

투자주의·상한가잔량·종가급변·전일 급등이 핵심 sealed 사실
→ MARKET_STATE_OR_CONTINUATION_CASE

유상증자·CB·CPS·감자·자사주·최대주주 변경이 핵심 sealed 사실
→ CAPITAL_ACTION_RESPONSE_CASE

cutoff 이전 검증 가능한 원인을 찾지 못함
→ NEWSLESS_OR_UNEXPLAINED
```

각 오류는 0.22의 사건·사실 국소성 계약을 통과해야 한다.

서로 다른 사건에 같은 correction 문구를 복사하지 않는다.

## 5.4 실제 승자 전수 연구

verified winner census의 모든 실제 승자를 분류한다.

```text
PREDICTABLE_DIRECT
PREDICTABLE_THEME
PREDICTABLE_CONTINUATION
INPUT_MISSING
ENTITY_MISSING
ROW_CLASSIFICATION_MISS
CANDIDATE_SCREENING_MISS
CANDIDATE_GENERATION_MISS
THEME_MAP_MISSING
LEADER_SELECTION_MISS
RANKING_MISS
TIMING_IMPOSSIBLE
NOVELTY_ERROR
MARKET_REGIME_MISS
NEWSLESS_OR_UNEXPLAINED
```

실제 승자마다 다음을 확인한다.

```text
입력 CSV 행 존재 여부
BLIND entity 존재 여부
candidate screening 존재 여부
BLIND concrete candidate 여부
BLIND final watchlist에 같은 ticker가 다른 event로 존재하는지
같은 issuer-day의 event 수와 대표 thesis
BLIND rank
cutoff 이전 외부 관계 증거
D 장중 신규 촉매
시장상태 공지인지 영업·산업 촉매인지
자본행위인지 운영·수주 사건인지
```

동일 ticker가 이미 watchlist에 있었다면 종목 누락으로 기록하지 않고 event/thesis 선택 오류로 분리한다.

## 5.5 Theme Formation Case

### 5.5.1 BLIND hypothesis case — sealed universe only

BLIND에 실제 존재한 theme hypothesis마다 봉인 전에 다음을 확정한다.

```text
theme_id
trigger_event_ids
formation_mechanism
sealed_candidate_ids
sealed_peer_tickers
sealed_archetypes
peer_membership_provenance
failure_conditions
```

OUTCOME 이후 theme 형성 여부는 **sealed peer universe 안에서만** 판정한다.

허용 `formation_status`:

```text
FORMED_IN_SEALED_UNIVERSE
PARTIAL_IN_SEALED_UNIVERSE
NOT_FORMED_IN_SEALED_UNIVERSE
UNSCORABLE_NO_SEALED_CONCRETE_PEERS
```

평가 필드:

```text
sealed peer 중 verified upper-limit count
sealed peer 중 verified high20 count
sealed peer 중 verified high10 count
sealed peer amount·turnover concentration
sealed peer actual leader
sealed peer negative controls
```

실제 승자가 결과 뒤 처음 발견됐거나 sealed peer universe에 없었다면, 그 승자를 넣어 기존 BLIND theme을 `FORMED`로 바꾸지 않는다.

입력 누락·cutoff 이후 뉴스·사후 새 관련주 때문에 오른 종목은 BLIND theme hit가 아니다.

### 5.5.2 retrospective discovered theme

결과 뒤 처음 발견한 theme 또는 peer는 별도 기록한다.

```text
record_type = retrospective_theme_discovery
training_target = theme_hypothesis_or_verified_member_edge_discovery
forecast_hit = false
```

retrospective theme을 만들 때 결과 상승 종목 목록만 저장하고 끝내지 않는다.

각 구성 종목에 0.23의 `retrospective_theme_member_edge`를 생성하고 다음을 분리한다.

```text
all_observed_members
verified_cutoff_members
unverified_members
after_cutoff_members
outcome_only_members
```

retrospective theme record 자체의 기본값:

```text
training_eligible = false
training_scope = HYPOTHESIS_ONLY
retrospective_memory_eligible = true 가능
```

개별 member edge만 cutoff provenance가 완전할 때 `training_eligible=true`가 될 수 있다.

모든 observed member를 하나의 학습 population으로 쓰려면:

```text
member_edge_coverage_ratio == 1.0
verified_cutoff_member_count == all_observed_member_count
after_cutoff_member_count == 0
outcome_only_member_count == 0
```

이어야 한다.

### 5.5.3 Theme hindsight·member provenance 게이트

```text
postseal_only_winner_used_to_upgrade_blind_theme_count == 0
after_cutoff_member_used_in_blind_theme_label_count == 0
input_missing_member_used_in_blind_theme_label_count == 0
sealed_peer_universe_mutation_after_outcome_count == 0
retrospective_theme_training_record_without_member_edges_count == 0
retrospective_theme_member_edge_missing_source_count == 0
retrospective_theme_member_edge_time_unverified_count == 0
retrospective_theme_member_edge_after_cutoff_marked_eligible_count == 0
retrospective_theme_outcome_only_member_marked_eligible_count == 0
retrospective_theme_relation_not_entailed_count == 0
retrospective_theme_full_population_coverage_error_count == 0
```

## 5.6 Beneficiary Discovery Case

실제 승자가 BLIND 구체 후보에 없었지만 cutoff 이전 관계 증거가 있었다면 별도 기록한다.

관계 상태:

```text
BLIND_CANDIDATE_EDGE
BLIND_ARCHETYPE_MATCH
POSTMORTEM_DISCOVERED_CUTOFF_EDGE
AFTER_CUTOFF_EDGE
OUTCOME_ONLY_ASSOCIATION
NO_VERIFIED_EDGE
```

각 beneficiary case에는 반드시 종목별 관계 edge가 있어야 한다.

```text
relation_statement
relation_class
source_ids
fact_ids
source_published_at
source_time_verified
available_before_cutoff
relation_known_at_cutoff
edge_origin
semantic_edge_audit_verdict
```

`POSTMORTEM_DISCOVERED_CUTOFF_EDGE`는 다음 날부터 수혜주 발굴 기억으로 사용할 수 있으나, 해당 날짜의 BLIND 적중으로 계산하지 않는다.

`AFTER_CUTOFF_EDGE`, `OUTCOME_ONLY_ASSOCIATION`, `NO_VERIFIED_EDGE`는 장전 수혜주 학습에 사용하지 않는다.

결과에서 같은 방향으로 올랐다는 사실은 관계 증거가 아니다.

## 5.7 Leader Pair를 엄격히 구분한다

### 5.7.1 sealed blind pair

정식 비교 모집단은 다음을 모두 만족해야 한다.

```text
두 종목 모두 BLIND concrete candidate pool에 존재
같은 sealed theme_id에 속함
pair가 outcome 전 봉인됨
두 종목 outcome label_quality == verified
hindsight-only 특징 분리
```

BLIND pair에는 다음을 저장한다.

```text
blind_pair_id
blind_preferred_candidate_id
blind_rejected_candidate_id
blind_preference_reason
blind_fact_ids
blind_inference_ids
safe_D1_features
```

### 5.7.2 outcome label

결과 공개 뒤 BLIND 선택과 정답 label을 분리한다.

```text
blind_preferred_candidate_id
blind_rejected_candidate_id
outcome_preferred_candidate_id
outcome_rejected_candidate_id
outcome_comparison_basis = high_return_pct_then_close_return_pct
blind_preference_correct
training_example_type = CONFIRMED_PREFERENCE | CORRECTION_PREFERENCE | TIE_OR_INCOMPARABLE
```

`preferred_ticker`라는 모호한 단일 필드를 사용하지 않는다.

BLIND 선호가 틀렸어도 record를 버릴 필요는 없다. `CORRECTION_PREFERENCE`로 저장하고 **outcome preferred candidate가 학습 target**이 되어야 한다.

동률·기업행위 차단·한쪽 outcome 미검증이면 `TIE_OR_INCOMPARABLE`, `training_eligible=false`다.

### 5.7.3 candidate generation error

실제 승자가 BLIND 후보에 없고 비교 상대만 후보였다면 leader pair로 저장하지 않는다.

```text
training_target = candidate_generation
record_type = candidate_generation_error_case
```

### 5.7.4 retrospective population pair

결과 뒤 새로 만든 pair는 기본적으로 leader training 불가다.

독립 pre-outcome population이 봉인돼 있었음을 증명하지 못하면:

```text
training_eligible = false
use_for = qualitative_postmortem_only
```

### 5.7.5 Pair 방향 검증

```text
pair_target_equals_outcome_winner_count == eligible_pair_count
blind_choice_stored_separately_count == eligible_pair_count
ambiguous_preferred_ticker_field_count == 0
wrong_direction_training_label_count == 0
```

## 5.8 같은 테마 전체 peer table

승자와 임의 패자 하나만 비교하지 않는다.

각 theme에 대해 가능한 모든 cutoff-available peer와 outcome을 표로 만든다.

```text
peer_universe_source
peer_count
winner_count
nonleader_count
quarantined_count
unresolved_count
```

## 5.9 후보 실패와 부정 대조군

모든 BLIND 후보와 issuer-day case를 검토한다.

결과가 약했다는 이유만으로 뉴스가 나빴다고 단정하지 않는다.

다음을 구분한다.

```text
뉴스 품질 문제
신규성 문제
귀속가치 문제
회사 체급 문제
선반영 문제
희석·오버행 문제
시장 regime 경쟁
테마 내 더 순수한 후보
무작위 수급·설명불가
```

## 5.10 Row·Entity 사후 오류 감사

실제 승자와 주요 강한 상승주를 BLIND row·entity·screening ledger와 대조한다.

BLIND 원본은 수정하지 않는다.

────────────────────────────────────────
6. 학습 적격성 결정
────────────────────────────────────────

## 6.1 bundle status

```text
ACCEPT_FULL
research_daily 전 시장 outcome 검증과 모든 핵심 모집단·쌍 품질 게이트 통과

ACCEPT_PARTIAL
BLIND는 완전하며 구현 결함은 0건이지만, 공식 최초 공개시각·상장 동일성·가격 label 같은 외부 데이터 한계 때문에 일부 연구영역만 구조적으로 제한됨

PENDING_OUTCOME
BLIND는 완전하나 research_daily outcome package 부재 또는 검증 실패

QUARANTINE
BLIND 오염·해시 오류·심각한 의미 오류
```

research_daily access와 snapshot이 정상이고 아래 게이트를 통과했다면 일부 가격만 읽었다는 이유로 `ACCEPT_PARTIAL`을 사용하지 않는다.

## 6.2 forecast_evaluation_eligible

```text
blind_valid == true
+ full_market_complete == true
+ upper_limit_census_complete == true
```

## 6.3 direct_population_training_eligible

```text
직접 observation screening coverage == 1.0
+ post-seal issuer resolution 완료
+ outcome_join_attempt_count == outcome_target_count
+ verified_or_quarantined_or_no_trade coverage == 1.0
+ accepted_issuer_false_positive_count == 0
+ training_eligible_entity_binding_error_count == 0
+ unresolved training issuer count == 0
```

구현 가능한 entity binding 오류를 `ACCEPT_PARTIAL`로 넘기지 않는다.

## 6.4 direct_record_training_eligible

각 record는 다음을 만족할 때 적격이다.

```text
resolved KR issuer
issuer-day outcome 존재
label_quality == verified
BLIND 특징과 hindsight 특징 분리
```

## 6.5 issuer_day_training_eligible

```text
동일 ticker-day 중복 통합 완료
issuer-day weight == 1
모든 event-level weight 합 == 1
가격 label verified
```

## 6.6 theme_formation_training_eligible

```text
blind_theme_hypothesis_exists == true
+ full_market_complete == true
+ cutoff 이전 trigger evidence 검증
+ theme peer membership provenance 존재
```

## 6.7 beneficiary_discovery_training_eligible

```text
실제 승자 관계가 cutoff 이전 출처로 검증
+ AFTER_CUTOFF_EDGE가 아님
+ hindsight-only 사실과 분리
```

## 6.8 blind_leader_pair_training_eligible

```text
두 종목 모두 BLIND candidate
+ same blind theme
+ blind pair sealed
+ 두 outcome verified
```

## 6.9 retrospective_pair_training_eligible

기본 false다.

독립 pre-outcome population 증명이 있을 때만 true 가능하다.

## 6.10 candidate_generation_error_training_eligible

```text
BLIND row·entity·screening 원본이 봉인돼 있음
+ 실제 winner 또는 강한 상승 outcome이 verified
+ 오류 phase·error_subject_scope·오류 유형이 명시됨
+ semantic_basis_fact_ids 또는 명시적 postmortem_fact_ids가 존재
+ 모든 correction clause에 support fact/source가 존재
+ 독립 semantic_audit_verdict == PASS
+ generic_template_used == false
+ correction 원리에 원 FACT에 없는 산업 개념이 없음
+ 수정 원리가 특정 티커 암기가 아님
```

## 6.10.1 candidate_ranking·event thesis error training eligibility

```text
BLIND candidate pool과 final watchlist가 봉인돼 있음
+ same_ticker_present_in_blind_pool와 same_ticker_present_in_final_watchlist를 계산
+ ticker가 이미 final watchlist에 있으면 issuer-level RANKING_MISS가 아님
+ EVENT_THESIS_SELECTION_MISS 또는 EVENT_ATTRIBUTION_MISS로 올바르게 분류
+ 선택된 event와 놓친 event의 fact/source가 모두 존재
+ 독립 semantic audit 통과
```

## 6.10.2 retrospective theme member edge training eligibility

```text
member별 edge_id 존재
+ source_ids·fact_ids 존재
+ source_time_verified == true
+ source_published_at <= cutoff_at
+ available_before_cutoff == true
+ relation_known_at_cutoff == true
+ edge_origin not in {AFTER_CUTOFF_SOURCE, OUTCOME_ONLY_ASSOCIATION}
+ outcome_used_as_relation_evidence == false
+ semantic_edge_audit_verdict == PASS
```

retrospective theme 전체 population 학습은 member edge coverage ratio가 1.0일 때만 가능하다.

## 6.11 entity_error_training_eligible

```text
BLIND entity ledger와 post-seal resolution이 모두 존재
+ false positive 또는 false negative 근거가 명확
+ ticker binding 오류와 entity type 오류를 구분
```

## 6.12 retrospective_memory_eligible

```text
BLIND 패킷이 깨끗하게 봉인됨
+ postmortem evidence의 시간·출처가 검증됨
+ 오류 교훈이면 사건·사실 국소성 및 독립 semantic audit 통과
+ retrospective theme이면 theme-level hypothesis와 member-level edge를 분리
+ training eligible member edge는 cutoff provenance를 완전 충족
+ memory claim이 tentative로 저장됨
+ available_from이 다음 실제 거래일 이상
```

## 6.13 brain_eligible

```text
training_eligible brain_delta record_count > 0
```

이면 true다.

## 6.14 ACCEPT_FULL 필수 조건

```text
BLIND 전체 해시 검증
row disposition 100%
직접 observation screening 100%
entity semantic gate 통과
blind snapshot 검증 통과
outcome snapshot 검증 통과
outcome_ledger row count == access outcome row count
full-market winner census 완료
모든 직접 issuer outcome join 시도 완료
issuer-day 중복 통합 완료
invalid blind leader pair 0
모든 training record provenance 존재
모든 training feature fact entailment 통과
cross-event·cross-issuer feature 누수 0
ID orphan 0
BLIND report outcome 누수 0
watchlist rank 연속성 통과
theme hindsight 분리 통과
leader pair target 방향 통과
사후 오류 교훈 semantic consistency 통과
generic correction template cross-signature reuse 0
동일 ticker의 issuer/event 오류 scope 오분류 0
retrospective theme member별 cutoff provenance 통과
outcome-only theme member training eligible 0
잘린 문장·템플릿 0
placeholder·미치환 변수 0
accepted issuer false positive 0
prefix·substring·group·venue·generic issuer 오결속 0
entity binding regression failure 0
canonical graph와 모든 파생 artifact count·hash 불일치 0
validator exit_code == 0
renderer·validator hash 기록 완료
```

────────────────────────────────────────
7. Brain Delta
────────────────────────────────────────

허용 record_type:

```text
supervised_issuer_day_case
supervised_direct_event_case
supervised_theme_formation_case
retrospective_theme_discovery
beneficiary_discovery_case
blind_leader_preference_pair
retrospective_population_pair
candidate_generation_error_case
candidate_ranking_error_case
event_thesis_selection_error_case
retrospective_theme_member_edge
row_disposition_error_case
entity_resolution_error_case
memory_claim
mechanism_memory
counterexample
event_ticker_edge
company_memory_delta
research_question
```

모든 record에 다음 공통 필드를 포함한다.

```text
record_id
episode_id
training_target
evidence_phase
training_eligible
eligibility_reason
available_from
provenance_source_ids
```

## 7.1 supervised_issuer_day_case

```json
{
  "record_type": "supervised_issuer_day_case",
  "record_id": "",
  "episode_id": "",
  "issuer_day_case_id": "",
  "trade_date": "",
  "ticker": "",
  "company_name": "",
  "event_ids": [],
  "combined_fact_ids": [],
  "combined_inference_ids": [],
  "safe_D1_features": {},
  "outcome": {},
  "response_class": "",
  "attribution_status": "SINGLE_EVENT_PLAUSIBLE | MULTI_EVENT_AMBIGUOUS | NO_CAUSAL_ATTRIBUTION",
  "sample_weight": 1.0,
  "training_target": "issuer_day_price_response",
  "evidence_phase": "SEALED_BLIND_FEATURES_PLUS_OUTCOME_LABEL",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

## 7.2 supervised_direct_event_case

```json
{
  "record_type": "supervised_direct_event_case",
  "record_id": "",
  "episode_id": "",
  "issuer_day_case_id": "",
  "event_id": "",
  "observation_id": "",
  "screening_id": "",
  "ticker": "",
  "company_name": "",
  "blind_fact_ids": [],
  "blind_inference_ids": [],
  "safe_D1_features": {},
  "outcome": {},
  "response_class": "",
  "sample_weight": 0.0,
  "label_quality": "verified | quarantined | no_tradable_row",
  "causal_attribution_status": "NOT_CLAIMED | PLAUSIBLE_ASSOCIATION | DIRECT_MARKET_ATTRIBUTION_EVIDENCE",
  "training_target": "event_outcome_association",
  "evidence_phase": "SEALED_BLIND_EVENT_PLUS_ISSUER_DAY_OUTCOME",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

## 7.3 supervised_theme_formation_case

```json
{
  "record_type": "supervised_theme_formation_case",
  "record_id": "",
  "episode_id": "",
  "theme_id": "",
  "trigger_event_ids": [],
  "blind_hypothesis": {},
  "sealed_candidate_ids": [],
  "sealed_peer_tickers": [],
  "sealed_peer_membership_provenance": [],
  "formation_status": "FORMED_IN_SEALED_UNIVERSE | PARTIAL_IN_SEALED_UNIVERSE | NOT_FORMED_IN_SEALED_UNIVERSE | UNSCORABLE_NO_SEALED_CONCRETE_PEERS",
  "verified_upper_limit_tickers_within_sealed_peers": [],
  "verified_high20_tickers_within_sealed_peers": [],
  "verified_high10_tickers_within_sealed_peers": [],
  "postseal_discovered_tickers_excluded_from_label": [],
  "breadth_metrics_within_sealed_peers": {},
  "outcome_scope": "FULL_MARKET_RESEARCH_DAILY",
  "training_target": "theme_formation_in_sealed_universe",
  "evidence_phase": "SEALED_BLIND_HYPOTHESIS_PLUS_FULL_MARKET_OUTCOME",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

## 7.4 beneficiary_discovery_case

```json
{
  "record_type": "beneficiary_discovery_case",
  "record_id": "",
  "episode_id": "",
  "theme_id": "",
  "event_ids": [],
  "ticker": "",
  "company_name": "",
  "relation_status": "BLIND_CANDIDATE_EDGE | BLIND_ARCHETYPE_MATCH | POSTMORTEM_DISCOVERED_CUTOFF_EDGE | AFTER_CUTOFF_EDGE | NO_VERIFIED_EDGE",
  "causal_chain_inference_ids": [],
  "cutoff_fact_ids": [],
  "cutoff_inference_ids": [],
  "outcome": {},
  "training_target": "beneficiary_discovery",
  "evidence_phase": "",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

## 7.5 blind_leader_preference_pair

```json
{
  "record_type": "blind_leader_preference_pair",
  "record_id": "",
  "episode_id": "",
  "blind_pair_id": "",
  "theme_id": "",
  "blind_preferred_candidate_id": "",
  "blind_rejected_candidate_id": "",
  "blind_preference_reason": "",
  "blind_fact_ids": [],
  "blind_inference_ids": [],
  "safe_D1_features": {},
  "outcome_preferred_candidate_id": "",
  "outcome_rejected_candidate_id": "",
  "outcome_comparison_basis": "high_return_pct_then_close_return_pct",
  "outcome_labels": {},
  "blind_preference_correct": false,
  "training_example_type": "CONFIRMED_PREFERENCE | CORRECTION_PREFERENCE | TIE_OR_INCOMPARABLE",
  "training_target_candidate_id": "",
  "training_target": "leader_selection_pairwise",
  "evidence_phase": "SEALED_BLIND_PAIR_PLUS_VERIFIED_OUTCOME_LABELS",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

## 7.6 candidate_generation_error_case

```json
{
  "record_type": "candidate_generation_error_case",
  "record_id": "",
  "episode_id": "",
  "error_subject_scope": "ROW | ENTITY | EVENT | ISSUER | THEME | LEADER",
  "row_ids": [],
  "sealed_entity_ids": [],
  "sealed_event_ids": [],
  "sealed_observation_ids": [],
  "sealed_screening_ids": [],
  "sealed_candidate_ids": [],
  "ticker": "",
  "company_name": "",
  "blind_decision": "",
  "same_ticker_present_in_blind_pool": false,
  "same_ticker_present_in_final_watchlist": false,
  "same_ticker_candidate_ids": [],
  "semantic_basis_fact_ids": [],
  "semantic_basis_inference_ids": [],
  "postmortem_fact_ids": [],
  "semantic_basis_source_ids": [],
  "actual_outcome": {},
  "error_type": "ROW_CLASSIFICATION_MISS | ENTITY_MISSING | ENTITY_FALSE_POSITIVE | TICKER_BINDING_ERROR | EVENT_CLUSTER_ERROR | CANDIDATE_SCREENING_MISS | CANDIDATE_GENERATION_MISS | RANKING_MISS | EVENT_THESIS_SELECTION_MISS | EVENT_ATTRIBUTION_MISS | MARKET_STATE_OR_CONTINUATION_CASE | CAPITAL_ACTION_RESPONSE_CASE | NEWSLESS_OR_UNEXPLAINED",
  "error_signature": {
    "event_family": "",
    "fact_predicate_types": [],
    "modality": "",
    "error_scope": "",
    "training_target": ""
  },
  "correction_principle": "",
  "correction_principle_clauses": [
    {
      "clause_id": "",
      "text": "",
      "support_fact_ids": [],
      "support_inference_ids": [],
      "support_source_ids": [],
      "support_phase": "SEALED_BLIND | POSTMORTEM_VERIFIED",
      "same_event_or_explicit_comparison": true,
      "entailment_verdict": "ENTAILED | SUPPORTED | UNSUPPORTED"
    }
  ],
  "counterfactual_action": "",
  "generic_template_used": false,
  "semantic_audit": {
    "verdict": "PASS | FAIL",
    "unsupported_concepts": [],
    "cross_event_concepts": [],
    "generic_template_suspected": false,
    "error_scope_correct": true,
    "error_type_correct": true,
    "same_ticker_scope_correct": true,
    "clause_support_complete": true,
    "failure_reasons": []
  },
  "training_target": "candidate_generation_or_event_thesis_selection",
  "evidence_phase": "POSTMORTEM_ERROR_ANALYSIS",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

## 7.6.1 candidate_ranking_error_case와 event_thesis_selection_error_case

같은 ticker가 어떤 형태로 BLIND 후보·watchlist에 존재했는지 먼저 판정한다.

```json
{
  "record_type": "candidate_ranking_error_case | event_thesis_selection_error_case",
  "record_id": "",
  "episode_id": "",
  "ticker": "",
  "company_name": "",
  "error_subject_scope": "ISSUER | EVENT",
  "existing_blind_candidate_ids": [],
  "existing_final_watchlist_candidate_ids": [],
  "selected_blind_event_ids": [],
  "missed_more_relevant_event_ids": [],
  "selected_thesis": "",
  "alternative_thesis": "",
  "semantic_basis_fact_ids": [],
  "postmortem_fact_ids": [],
  "actual_outcome": {},
  "correction_principle_clauses": [],
  "semantic_audit": {},
  "training_target": "ranking | event_thesis_selection",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

같은 ticker가 final watchlist에 이미 존재하면 `record_type=candidate_ranking_error_case`, `error_subject_scope=ISSUER`로 저장할 수 없다.

## 7.6.2 모든 감독학습 레코드의 의미 근거 계약

다음 필드는 금지한다.

```text
근거 없는 자유 feature dictionary
템플릿에서 자동 삽입한 산업 태그
다른 event에서 복사된 고객·제품·계약 특징
결과를 본 뒤 새로 쓴 BLIND feature
```

모든 감독학습 레코드는 다음을 가져야 한다.

```text
blind_fact_ids
blind_inference_ids
fact_entailment_verified = true
cross_event_leak_verified = true
```

`training_eligible=true`가 되려면 참조된 fact가 모두 `ENTAILED`, inference가 모두 `SUPPORTED` 또는 허용된 `WEAKLY_SUPPORTED`여야 한다.

사후 오류 레코드는 추가로 다음을 만족해야 한다.

```text
모든 correction clause가 같은 사건 FACT 또는 명시적 비교 FACT를 참조
원 FACT에 없는 산업·고객·공급망·정책 개념 0
서로 다른 error_signature 사이 generic correction 재사용 0
독립 semantic audit PASS
동일 ticker 후보 존재 여부에 맞는 오류 scope
```

## 7.6.3 retrospective_theme_discovery와 member edge 계약

```json
{
  "record_type": "retrospective_theme_discovery",
  "record_id": "",
  "episode_id": "",
  "retrospective_theme_id": "",
  "theme_name": "",
  "discovery_basis": "FULL_MARKET_OUTCOME_BREADTH",
  "forecast_hit": false,
  "all_observed_member_tickers": [],
  "verified_cutoff_member_edge_ids": [],
  "ineligible_member_tickers": [],
  "member_edge_coverage_ratio": 0.0,
  "training_scope": "HYPOTHESIS_ONLY | VERIFIED_MEMBER_EDGES_ONLY | FULL_MEMBER_POPULATION",
  "training_target": "retrospective_theme_hypothesis",
  "training_eligible": false,
  "retrospective_memory_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

각 member는 별도 edge로 저장한다.

```json
{
  "record_type": "retrospective_theme_member_edge",
  "record_id": "",
  "episode_id": "",
  "edge_id": "",
  "retrospective_theme_id": "",
  "ticker": "",
  "company_name": "",
  "relation_class": "DIRECT | FUNDAMENTAL | MARKET_MEMORY | CONTINUATION | INFERRED_NEW",
  "relation_statement": "",
  "relation_mechanism": [],
  "source_ids": [],
  "fact_ids": [],
  "inference_ids": [],
  "source_published_at": [],
  "source_time_verified": true,
  "available_before_cutoff": true,
  "relation_known_at_cutoff": true,
  "edge_origin": "CSV_INPUT | PRIOR_CLEAN_MEMORY | POSTMORTEM_CUTOFF_SOURCE | AFTER_CUTOFF_SOURCE | OUTCOME_ONLY_ASSOCIATION",
  "outcome_used_to_discover": true,
  "outcome_used_as_relation_evidence": false,
  "semantic_edge_audit": {
    "verdict": "PASS | FAIL",
    "relation_entailed": true,
    "source_scope_correct": true,
    "time_scope_correct": true,
    "outcome_leak": false,
    "failure_reasons": []
  },
  "training_target": "beneficiary_or_theme_member_edge_discovery",
  "training_eligible": true,
  "eligibility_reason": "",
  "available_from": "",
  "provenance_source_ids": []
}
```

`retrospective_theme_discovery`는 결과에서 발견한 가설 기억이며, member edge를 자동으로 대표하지 않는다.

`FULL_MEMBER_POPULATION`은 모든 observed member의 edge가 cutoff-valid일 때만 허용한다.

## 7.7 memory claim

한 episode로 법칙을 확정하지 않는다.

```text
status = tentative
confidence_label = low
```

D 결과를 보고 생성한 모든 교훈은 다음 실제 거래일부터 사용 가능하다.

## 7.8 오류·반례·기억 레코드

다음 레코드는 가격 예측 표본과 별개로 보존한다.

```text
candidate_ranking_error_case
row_disposition_error_case
entity_resolution_error_case
counterexample
mechanism_memory
memory_claim
event_ticker_edge
company_memory_delta
research_question
```

오류 레코드가 training eligible이 되려면 다음을 만족해야 한다.

```text
실제 BLIND ledger와 사후 결과를 모두 참조
오류가 어느 phase·어느 scope에서 발생했는지 명시
모든 교정 clause가 fact/source로 지지됨
독립 semantic audit PASS
원 사건과 무관한 산업 메커니즘 삽입 0
서로 다른 semantic signature에 generic correction 복사 0
동일 ticker의 다른 BLIND event 존재 여부에 맞는 오류 taxonomy
수정 원리가 특정 종목 암기나 고정 키워드 규칙이 아님
cutoff 이후 사실과 cutoff 이전 사실을 분리
```

retrospective theme·beneficiary 관계 레코드는 종목별 cutoff provenance가 없으면 training eligible이 될 수 없다.

## 7.9 available_from와 기억 승격

D 결과를 보고 생성한 모든 Brain Delta의 기본 `available_from`은 다음 실제 거래일이다.

```text
available_from = next_trade_date
```

한 episode에서 나온 일반화는 기본값을 다음으로 둔다.

```text
status = tentative
confidence_label = low
```

여러 episode에서 지지·반박이 누적되기 전에는 validated 법칙으로 선언하지 않는다.

## 7.10 Blind Prediction JSON 계약

`blind_prediction.json`은 최소한 다음 최상위 구조를 가진다.

```json
{
  "schema_version": "nslab.blind_prediction.v11",
  "episode_id": "",
  "trade_date": "",
  "previous_trade_date": "",
  "window_start": "",
  "cutoff_at": "",
  "created_at": "",
  "status": "BLIND_SEALED",
  "input_audit": {},
  "research_daily_blind_context": {},
  "blind_integrity": {},
  "row_disposition_summary": {},
  "entity_quality_summary": {},
  "fact_quality_summary": {},
  "inference_quality_summary": {},
  "candidate_screening_summary": {},
  "open_world_first_read": {},
  "event_clusters": [],
  "all_direct_event_observations": [],
  "dominant_sector_hypotheses": [],
  "theme_candidate_archetypes": [],
  "sealed_theme_peer_universes": [],
  "single_event_candidates": [],
  "theme_beneficiary_candidates": [],
  "continuation_candidates": [],
  "blind_pairwise_preferences": [],
  "final_watchlist": [],
  "excluded_but_notable": [],
  "blind_limitations": []
}
```

결과 공개 뒤 이 객체의 어떤 필드도 수정하지 않는다.

## 7.11 Source Ledger 계약

`source_ledger.jsonl`에는 실제 판단에 사용한 출처만 기록한다.

원본 뉴스 CSV 전체는 input SHA와 row ID로 추적하고, 모든 1,000여 행의 본문을 source ledger에 중복 복제하지 않는다.

각 행:

```json
{
  "source_id": "SRC-000001",
  "source_type": "news_csv_row | research_daily_manifest | research_daily_schema | research_daily_access | research_daily_blind_snapshot | research_daily_outcome_snapshot | official_disclosure | official_release | company_release | news_article | prior_clean_episode",
  "title": "",
  "publisher": "",
  "url": "",
  "published_at": null,
  "retrieved_at": "",
  "time_verified": true,
  "available_before_cutoff": true,
  "usage_phase": "BLIND | POSTMORTEM | BOTH",
  "input_row_ids": [],
  "content_sha256": "",
  "notes": ""
}
```

가격 snapshot source에는 다음을 `notes` 또는 구조화 필드에 기록한다.

```text
snapshot_date
expected_sha256
actual_sha256
expected_row_count
actual_row_count
max_source_date
```

## 7.12 단일 Markdown front matter

최종 bundle의 YAML front matter에는 최소한 다음을 기록한다.

```yaml
schema_version: nslab.research_bundle.v11
artifact_type: research_episode_bundle
episode_id: <EPISODE_ID>
trade_date: <TRADE_DATE>
window_start: <WINDOW_START>
cutoff_at: <CUTOFF_AT>
input_file: <INPUT_FILE>
input_sha256: <INPUT_SHA256>
execution_protocol_version: nslab.brain_grade_semantic_provenance_locked.v11
bundle_status: <ACCEPT_FULL_OR_OTHER>
blind_valid: true
blind_packet_manifest_sha256: <SHA256>
sealed_blind_report_sha256: <SHA256>
research_daily_access_sha256: <SHA256>
blind_snapshot_sha256: <SHA256>
outcome_snapshot_sha256: <SHA256>
canonical_graph_sha256: <SHA256>
renderer_version: <VERSION>
renderer_sha256: <SHA256>
validator_version: <VERSION>
validator_sha256: <SHA256>
validator_exit_code: 0
direct_brain_ingest_ready: true
requires_manual_research_review: false
requires_posthoc_prompt_repair: false
automated_import_expected_to_pass: true
created_at: <CREATED_AT>
```

────────────────────────────────────────
8. Research Episode JSON
────────────────────────────────────────

최상위 구조:

```json
{
  "schema_version": "nslab.research_episode.v11",
  "episode_id": "",
  "trade_date": "",
  "previous_trade_date": "",
  "next_trade_date": "",
  "window_start": "",
  "cutoff_at": "",
  "created_at": "",
  "execution_protocol_version": "nslab.brain_grade_semantic_provenance_locked.v11",
  "bundle_status": "ACCEPT_FULL | ACCEPT_PARTIAL | PENDING_OUTCOME | QUARANTINE",
  "blind_valid": true,
  "blind_packet_manifest_sha256": "",
  "input_news_files": [],
  "input_news_hashes": {},
  "input_audit": {},
  "research_daily_source": {
    "manifest_url": "",
    "manifest_sha256": "",
    "schema_url": "",
    "schema_sha256": "",
    "access_url": "",
    "access_sha256": "",
    "blind_snapshot_path": "",
    "blind_snapshot_sha256": "",
    "blind_snapshot_row_count": 0,
    "outcome_snapshot_path": "",
    "outcome_snapshot_sha256": "",
    "outcome_snapshot_row_count": 0,
    "price_adjustment_status": "raw_unadjusted_marcap",
    "markets": ["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"]
  },
  "row_disposition_summary": {},
  "entity_quality_summary": {},
  "fact_quality_summary": {},
  "inference_quality_summary": {},
  "candidate_screening_summary": {},
  "blind_analysis": {},
  "blind_predictions": {},
  "entity_resolution_summary": {},
  "outcome_status": "",
  "outcome_completeness_audit": {},
  "winner_census": {},
  "issuer_day_cases": [],
  "direct_event_cases": [],
  "theme_formation_cases": [],
  "beneficiary_discovery_cases": [],
  "blind_leader_pairs": [],
  "retrospective_pairs": [],
  "row_entity_candidate_errors": [],
  "negative_controls": [],
  "postmortem": {},
  "eligibility_matrix": {},
  "brain_delta_summary": {},
  "available_from": "",
  "id_registry_summary": {},
  "validation_summary": {},
  "provenance": {}
}
```

────────────────────────────────────────
9. 사람이 읽는 연구 보고서
────────────────────────────────────────

사람용 보고서는 두 파일을 물리적으로 분리해 만든다.

```text
blind/blind_report.md
postseal_outcome/postmortem_report.md
```

두 보고서는 수동 작성본이 아니라 canonical graph를 입력으로 하는 renderer의 산출물이어야 한다.

```text
render_nslab_bundle.py --phase blind
render_nslab_bundle.py --phase postmortem
```

표의 row, rank, count, ticker, 수익률, eligibility는 canonical JSON/JSONL에서 직접 렌더링한다.

다음을 금지한다.

```text
보고서 표를 별도로 손으로 다시 작성
JSON과 다른 숫자를 산문에 입력
미치환 f-string·Jinja·Python 표현식 남김
후보 삭제 뒤 rank를 수동 수정하지 않음
```

`blind_report.md`는 BLIND packet과 함께 outcome 전에 봉인한다.

`research_report.md`는 결과 공개 뒤 다음 방식으로만 생성한다.

```text
read_bytes(blind_report.md)
+ "\n\n--- BLIND 봉인 이후 결과 공개 ---\n\n"
+ read_bytes(postmortem_report.md)
```

BLIND 보고서 순서:

```text
# 연구 episode 개요 — BLIND

## 1. 입력·거래일 감사
## 2. research_daily access·schema 검증
## 3. BLIND snapshot 안전성·해시 검증
## 4. BLIND 무결성·패킷 봉인
## 5. 뉴스 행 전수 분류 커버리지
## 6. BLIND 엔티티 의미 정확도
## 7. Atomic Fact·Inference 품질
## 8. 직접 기업뉴스 관측 모집단
## 9. 모든 observation 후보 심사
## 10. 사건 지도
## 11. 오픈월드 최초 분석
## 12. 주도섹터 가설과 sealed peer universe
## 13. 단일뉴스 후보
## 14. 테마 수혜 archetype·후보
## 15. D-1 연속성 후보
## 16. BLIND pairwise 비교
## 17. 최종 장전 관심종목
## 18. BLIND Red-team
## 19. BLIND packet manifest
```

이 구간에는 D 결과·적중 여부·response column을 넣지 않는다.

최종 장전 관심종목 rank는 1부터 N까지 연속돼야 한다.

POSTMORTEM 보고서 순서:

```text
# POSTMORTEM

## 20. OUTCOME snapshot 완전성·해시 검증
## 21. Post-seal 엔티티 확정
## 22. 전 시장 상한가·강한 상승 census
## 23. forecast scorecard
## 24. issuer-day 감독학습 모집단
## 25. 직접뉴스 event-level 감독학습 모집단
## 26. 후보 생성·순위·event thesis 오류
- 각 오류마다 sealed FACT 요약, error_subject_scope, error_type, same-ticker BLIND 존재 여부, correction clause별 support를 표로 렌더링한다.
- generic correction 문구를 쓰지 않는다.

## 27. 주도섹터 형성 연구 — sealed universe 기준
## 28. retrospective theme discovery
- theme별 observed member와 verified cutoff member를 분리한다.
- 각 종목의 source_id, published_at, time_verified, relation_class, edge_origin, semantic audit, training eligibility를 표로 렌더링한다.

## 29. 수혜주 발견 연구
## 30. 대장 선택 correction·confirmation 연구
## 31. 후보 실패·부정 대조군
## 32. 행·엔티티·ticker binding 오류
## 33. 학습 적격성 매트릭스
## 34. Brain Delta 요약
## 35. 다음 연구 질문
## 36. 출처·데이터 한계
```

보고서 validator는 separator 전 영역에 outcome field가 존재하는지 검사한다.

────────────────────────────────────────
10. 최종 품질 게이트
────────────────────────────────────────

## 10.0 실행형 preflight validator

최종 품질 게이트는 `validate_nslab_bundle.py`의 계산 결과로만 판정한다.

validator는 다음 순서로 실행한다.

```text
1. 내부 논리 artifact 전부 읽기
2. draft bundle의 모든 NSLAB block 추출
3. 내부 artifact와 embedded block byte/hash 비교
4. JSON/JSONL 전수 parse
5. canonical graph와 파생 artifact의 object count·field 일치 비교
6. 모든 정적·논리·의미 gate 계산
7. validation_report.json 생성
8. exit code 반환
```

### 10.0.1 placeholder·template scanner

다음 대상을 전수 검사한다.

```text
blind_report.md
postmortem_report.md
research_report.md
모든 JSON/JSONL string leaf
YAML front matter
```

최종 산출물에서 금지되는 pattern 예:

```text
{exact_duplicate_count}
{input_audit[...]} 
${...}
{{ ... }}
<EPISODE_ID>
<TRADE_DATE>
TODO
TBD
FIXME
PLACEHOLDER
INSERT_HERE
```

실제 validator는 최소 다음 정규식을 사용한다.

```text
\{[A-Za-z_][^{}\n]{0,200}\}
\{[^{}\n]*\[[^\]]+\][^{}\n]*\}
\$\{[^}\n]+\}
\{\{[^}\n]+\}\}
<[A-Z][A-Z0-9_]{2,}>
\b(?:TODO|TBD|FIXME|PLACEHOLDER|INSERT_HERE)\b
```

JSON 객체의 구조용 `{` `}`는 검사 대상이 아니며, 파싱된 **string value**만 검사한다.

추가로 다음을 검사한다.

```text
unbalanced_quote_count == 0
unbalanced_bracket_count == 0
known_generic_fallback_phrase_count == 0
truncated_narrative_field_count == 0
empty_required_narrative_count == 0
```

정보가 부족하면 `null`, 빈 배열, `UNRESOLVED`를 사용하고 범용 문구로 채우지 않는다.

### 10.0.2 검증 결과 증거 계약

각 check는 다음 구조를 가진다.

```json
{
  "check_id": "CHECK-...",
  "passed": true,
  "actual_count": 0,
  "expected": "== 0",
  "affected_ids": [],
  "artifact_paths": [],
  "computed_by": "validate_nslab_bundle.py"
}
```

`passed=true`인데 `actual_count`·`expected`·`computed_by`가 없는 check는 무효다.

## 10.1 BLIND 무결성

```text
blind_valid == true
no_D_outcome_exposed == true
outcome_snapshot_download_before_seal == false
모든 BLIND 파일 hash 검증
embedded BLIND bytes == sealed bytes
```

## 10.2 연구 입력·가격 모집단 완전성

```text
row_disposition_coverage_ratio == 1.0
unscreened_direct_observation_count == 0
all accepted issuer entities linked to observation
blind snapshot row count == access expected row count
outcome snapshot row count == access expected row count
outcome_ledger row count == outcome snapshot row count
outcome_join_attempt_count == outcome_target_count
```

## 10.3 엔티티 의미·binding 품질

```text
article_level_ticker_propagation_count == 0
accepted_noncorporate_as_issuer_count == 0
prefix_binding_count == 0
suffix_binding_count == 0
substring_binding_count == 0
fuzzy_binding_count == 0
group_to_member_binding_count == 0
brand_or_product_to_issuer_binding_count == 0
place_or_venue_to_issuer_binding_count == 0
generic_phrase_to_issuer_binding_count == 0
entity_binding_regression_failure_count == 0
accepted_issuer_false_positive_count == 0
training_eligible_entity_binding_error_count == 0
```

비율 허용치를 두지 않는다.

`ACCEPT_FULL`과 전체 직접뉴스 모집단 학습은 **accepted issuer false positive 0건**일 때만 가능하다.

정확한 binding을 만들 수 없는 mention은 억지로 붙이지 않고 unresolved로 보존한다.

## 10.4 가격 라벨 품질

```text
upper_limit_label_status를 직접 존중
corporate_action_warning 행 차단
new_listing_or_no_reference 행 차단
blocked label을 false로 강제하지 않음
verified와 quarantined 분리
```

## 10.5 issuer-day 중복 방지

```text
unique issuer_day_case_id 수 == 고유 trade_date+ticker 수
각 issuer-day sample_weight == 1
각 issuer-day의 event-level weight 합 == 1
동일 ticker-day가 독립 outcome 표본으로 중복 집계되지 않음
```

## 10.6 leader pair 무결성

```text
invalid_blind_leader_pair_count == 0
winner_absent_from_blind_pair_count == 0
postseal_cherrypicked_pair_count == 0
```

실제 승자가 BLIND 후보에 없었다면 leader pair가 아니라 candidate generation error다.

## 10.7 Brain Delta 품질

```text
모든 training_eligible record에 verified label 또는 명시적 비가격 학습 target
모든 record에 training_target
모든 record에 provenance
BLIND 특징과 hindsight-only 특징 분리
한 episode 교훈을 validated 법칙으로 선언하지 않음
모든 뉴스 특징이 fact_id 또는 inference_id를 참조
근거 없는 자유 feature tag 없음
```


## 10.7.1 의미 특징 근거 품질

```text
fact_quote_not_found_count == 0
fact_offset_mismatch_count == 0
training_fact_not_entailed_count == 0
training_inference_unsupported_count == 0
cross_event_feature_leak_count == 0
cross_issuer_feature_leak_count == 0
feature_without_reference_count == 0
incomplete_training_text_count == 0
```

## 10.7.2 Theme hindsight 분리

```text
sealed_peer_universe_mutation_after_outcome_count == 0
postseal_winner_used_to_upgrade_blind_theme_count == 0
after_cutoff_member_used_in_blind_theme_label_count == 0
retrospective_theme_record_mixed_into_forecast_hit_count == 0
```

## 10.7.3 보고서·순위 경계

```text
embedded_blind_report_hash_match == true
report_phase_leak_count == 0
separator_count == 1
watchlist_rank_gap_count == 0
watchlist_rank_duplicate_count == 0
watchlist_candidate_duplicate_count == 0
```

## 10.7.4 Pair label 방향

```text
ambiguous_preferred_ticker_field_count == 0
eligible_pair_target_not_equal_outcome_winner_count == 0
blind_choice_not_separated_from_target_count == 0
incomparable_pair_marked_eligible_count == 0
```

## 10.7.5 사후 오류 교훈 의미정합성

구조 검증과 독립 의미 감사를 함께 수행한다.

```text
correction_record_missing_semantic_basis_count == 0
correction_clause_without_support_count == 0
correction_support_fact_missing_count == 0
correction_support_source_missing_count == 0
correction_fact_event_mismatch_count == 0
correction_cross_issuer_leak_count == 0
unsupported_domain_concept_in_correction_count == 0
generic_correction_reuse_across_signature_count == 0
semantic_audit_fail_count == 0
market_state_notice_miscast_as_operating_catalyst_count == 0
capital_action_miscast_as_supply_chain_or_operating_catalyst_count == 0
unexplained_move_given_unverified_causal_lesson_count == 0
```

정규화한 correction 문구가 반복되면 `error_signature`, fact predicate, modality, error scope가 모두 동일한지 검사한다.

하나라도 다르면 범용 템플릿 복사로 판정한다.

## 10.7.6 동일 ticker의 issuer/event 오류 scope

```text
issuer_omission_claim_for_ticker_present_in_blind_pool_count == 0
issuer_ranking_miss_for_ticker_present_in_final_watchlist_count == 0
same_ticker_other_event_not_reclassified_to_event_thesis_miss_count == 0
event_thesis_miss_without_both_event_fact_sets_count == 0
event_attribution_miss_without_issuer_day_multi_event_count == 0
```

같은 ticker가 final watchlist에 존재하면 “종목을 놓쳤다”는 교훈을 만들 수 없다.

## 10.7.7 retrospective theme 종목별 provenance

```text
retrospective_theme_training_record_without_member_edges_count == 0
retrospective_theme_member_edge_missing_source_count == 0
retrospective_theme_member_edge_missing_fact_count == 0
retrospective_theme_member_edge_time_unverified_count == 0
retrospective_theme_member_edge_after_cutoff_marked_eligible_count == 0
retrospective_theme_outcome_only_member_marked_eligible_count == 0
retrospective_theme_outcome_used_as_relation_evidence_count == 0
retrospective_theme_relation_not_entailed_count == 0
retrospective_theme_semantic_edge_audit_fail_count == 0
retrospective_theme_full_population_coverage_error_count == 0
retrospective_theme_empty_blind_fact_training_eligible_count == 0
```

`training_scope=FULL_MEMBER_POPULATION`이면 `member_edge_coverage_ratio == 1.0`이어야 한다.

`training_scope=VERIFIED_MEMBER_EDGES_ONLY`이면 검증된 edge만 Brain Delta에 학습 적격으로 들어가고 나머지 종목은 명시적으로 ineligible 상태를 유지한다.

## 10.8 번들 검증

```text
모든 BEGIN/END marker 정확히 1회
모든 JSON parse 성공
모든 JSONL line parse 성공
모든 ID 정의 unique
모든 ID 참조 유효
모든 source_id 존재
blind packet hash 일치
blind_report hash 일치
access·snapshot hash 일치
bundle_manifest의 크기·SHA-256 일치
id_registry와 실제 artifact 일치
validation_report critical_error_count == 0
direct_ingest_contract 존재·JSON parse 성공
direct_ingest_contract record count/hash가 실제 artifact와 일치
direct_brain_ingest_ready가 validator 계산 결과와 일치
```

검증기는 실제 artifact를 읽어 계산한다. LLM의 자기 선언을 검증 결과로 사용하지 않는다.

## 10.9 과거 실패 재발 방지 회귀 잠금

최종 저장 전에 다음을 코드로 감사한다.

```text
latest/symbol_profile/all_symbols/current_symbols 접근 횟수 == 0
BLIND 전 outcome snapshot 다운로드 횟수 == 0
개별 종목 shard 다운로드 횟수 == 0
포털 TOP30을 outcome census로 사용한 횟수 == 0
outcome_coverage_status == FULL_MARKET_RESEARCH_DAILY 또는 PENDING_OUTCOME
부분 종목 결과를 full market로 선언한 횟수 == 0
모든 뉴스 행 disposition coverage == 1.0
모든 직접 observation screening coverage == 1.0
일반명사·인물·스포츠 엔티티의 상장사 승인 수 == 0
결과 뒤 새로 만든 pair의 blind leader training 수 == 0
동일 issuer-day outcome 중복 표본 수 == 0
첫 뉴스가 몇 초 늦다는 이유만으로 coverage gap을 만든 수 == 0
corporate action 차단 라벨을 정상 false label로 바꾼 수 == 0
training feature entailment 실패 수 == 0
cross-event 또는 cross-issuer feature 누수 수 == 0
orphan ID 참조 수 == 0
BLIND 보고서 outcome 누수 수 == 0
watchlist rank 누락·중복 수 == 0
사후 winner로 BLIND theme 형성 판정을 상향한 수 == 0
leader pair target 방향 오류 수 == 0
불완전 문장·잘린 템플릿 수 == 0
placeholder·미치환 변수 수 == 0
prefix·suffix·substring·fuzzy ticker binding 수 == 0
group·brand·venue·product·generic phrase issuer 오인 수 == 0
post-seal accepted issuer false positive 수 == 0
entity binding regression failure 수 == 0
canonical graph와 파생 artifact count mismatch 수 == 0
manifest self-declaration과 validator 계산 불일치 수 == 0
사후 correction clause support 누락 수 == 0
원 FACT에 없는 산업 개념을 교정문에 삽입한 수 == 0
서로 다른 error signature에 동일 generic correction을 사용한 수 == 0
동일 ticker가 final watchlist에 있는데 issuer-level RANKING_MISS로 기록한 수 == 0
retrospective theme member edge source 누락 수 == 0
cutoff 이후 관계 edge를 training eligible로 둔 수 == 0
outcome-only 동반상승을 수혜관계로 training eligible 처리한 수 == 0
```

하나라도 위반하면 `ACCEPT_FULL`로 저장하지 않는다.

## 10.10 의미·provenance synthetic regression fixture

최종 validator는 실제 episode 데이터와 별도로 다음 가상 fixture를 실행한다.

```text
FIXTURE-A
봉인 FACT = 투자주의 상한가잔량 공지
제안 교정문 = AI 공급망 병목을 심사해야 한다
기대 = FAIL_UNSUPPORTED_DOMAIN_CONCEPT

FIXTURE-B
봉인 FACT = CB·CPS·유상증자
제안 교정문 = 고객점유율과 공급물량을 봐야 한다
기대 = FAIL_CROSS_MECHANISM_TEMPLATE

FIXTURE-C
같은 ticker가 final watchlist에 EVENT-A로 존재
실제 더 직접적인 EVENT-B가 별도로 존재
제안 오류 = issuer-level RANKING_MISS
기대 = FAIL_WRONG_ERROR_SCOPE, 정답 EVENT_THESIS_SELECTION_MISS

FIXTURE-D
retrospective theme member에 source_id 없음
기대 = member training_eligible false

FIXTURE-E
관계 source published_at > cutoff_at
기대 = AFTER_CUTOFF_EDGE, training_eligible false

FIXTURE-F
유일한 관계 근거가 D outcome에서 함께 상승했다는 사실
기대 = OUTCOME_ONLY_ASSOCIATION, training_eligible false

FIXTURE-G
cutoff 이전 공식 source가 종목의 사업·지역·공급망 관계를 직접 지지
기대 = semantic edge PASS, 개별 member edge training eligible 가능
```

다음 값이 모두 0이어야 한다.

```text
semantic_type_fixture_failure_count
retrospective_provenance_fixture_failure_count
error_scope_fixture_failure_count
```

────────────────────────────────────────
11. Bundle Manifest
────────────────────────────────────────

최소:

```json
{
  "schema_version": "nslab.bundle_manifest.v11",
  "episode_id": "",
  "trade_date": "",
  "created_at": "",
  "execution_protocol_version": "nslab.brain_grade_semantic_provenance_locked.v11",
  "input_sha256": "",
  "blind_packet_manifest_sha256": "",
  "sealed_blind_report_sha256": "",
  "research_daily_access_sha256": "",
  "blind_snapshot_sha256": "",
  "outcome_snapshot_sha256": "",
  "blind_snapshot_row_count": 0,
  "outcome_snapshot_row_count": 0,
  "outcome_ledger_row_count": 0,
  "fact_record_count": 0,
  "inference_record_count": 0,
  "issuer_day_case_count": 0,
  "direct_event_case_count": 0,
  "brain_delta_record_count": 0,
  "training_eligible_record_count": 0,
  "canonical_graph_sha256": "",
  "renderer_version": "",
  "renderer_sha256": "",
  "validator_version": "",
  "validator_sha256": "",
  "validator_exit_code": 0,
  "entity_binding_regression_test_count": 0,
  "entity_binding_regression_failure_count": 0,
  "semantic_type_fixture_failure_count": 0,
  "retrospective_provenance_fixture_failure_count": 0,
  "error_scope_fixture_failure_count": 0,
  "postmortem_semantic_audit_record_count": 0,
  "retrospective_theme_member_edge_count": 0,
  "placeholder_token_count": 0,
  "accepted_issuer_false_positive_count": 0,
  "embedded_blocks": {},
  "eligibility_matrix": {},
  "validation": {
    "json_valid": true,
    "jsonl_valid": true,
    "markers_complete": true,
    "blind_packet_hash_verified": true,
    "blind_report_hash_verified": true,
    "research_daily_access_verified": true,
    "blind_snapshot_hash_verified": true,
    "outcome_snapshot_hash_verified": true,
    "full_market_complete": true,
    "issuer_day_dedup_verified": true,
    "semantic_fact_entailment_valid": true,
    "cross_event_feature_leak_zero": true,
    "id_references_valid": true,
    "source_references_valid": true,
    "theme_hindsight_separation_valid": true,
    "leader_pair_direction_valid": true,
    "postmortem_correction_semantic_consistency_valid": true,
    "issuer_event_error_scope_valid": true,
    "retrospective_theme_member_provenance_valid": true,
    "outcome_only_relation_training_zero": true,
    "semantic_type_fixtures_valid": true,
    "report_phase_boundary_valid": true,
    "rank_sequence_valid": true,
    "text_completeness_valid": true,
    "placeholder_scan_valid": true,
    "strict_entity_binding_valid": true,
    "entity_regression_valid": true,
    "canonical_graph_consistency_valid": true,
    "validator_exit_code": 0,
    "critical_error_count": 0
  }
}
```

`validation_report.json`은 각 검사 항목의 실제 count·오류 ID·수리 이력을 가진다.

각 critical check는 `actual`, `expected`, `expected_source`, `severity`, `error_ids`를 반드시 기록한다. `expected_source`가 `GENERATED_OUTPUT` 또는 `SELF_DECLARED_MANIFEST`이면 validator self-reference 오류로 처리한다.

다음 check는 누락되면 critical error다.

```text
final_watchlist_size_lte_20
final_watchlist_max_rank_lte_20
final_watchlist_rank_sequence_1_to_N
final_candidate_source_fact_present
weak_final_reason_zero
brain_delta_record_count_verified
validator_expected_source_not_generated_output
schema_contract_verified
canonical_graph_consistency_verified
```


## 11.1 ID Registry 계약

```json
{
  "schema_version": "nslab.id_registry.v1",
  "definitions": [
    {
      "id": "EVT-000001",
      "id_type": "EVENT",
      "artifact": "blind_prediction.json",
      "location": "event_clusters[0]"
    }
  ],
  "duplicate_defined_id_count": 0,
  "orphan_reference_count": 0,
  "wrong_id_type_reference_count": 0,
  "source_reference_missing_count": 0
}
```

## 11.2 Validation Report 계약

```json
{
  "schema_version": "nslab.validation_report.v3",
  "validator_version": "nslab.validator.v3",
  "validator_sha256": "",
  "executed_at": "",
  "exit_code": 0,
  "repair_attempt_count": 0,
  "checked_artifact_hashes": {},
  "checks": {},
  "critical_errors": [],
  "noncritical_warnings": [],
  "repairs": [],
  "critical_error_count": 0,
  "accept_full_allowed": true,
  "direct_ingest_ready": true,
  "direct_ingest_contract_sha256": "",
  "repair_loop_executed_or_not_needed": true,
  "remaining_fixable_critical_error_count": 0
}
```

검증 오류가 있으면 원인 ID를 구체적으로 남기고 자동 수리 후 전체 검사를 다시 수행한다.

────────────────────────────────────────
12. 이번 연구에서 반드시 답할 핵심 질문
────────────────────────────────────────

```text
1. 어떤 직접 기업뉴스가 verified 상한가·고가 +20%·+10%를 만들었는가?
2. 비슷한 직접 기업뉴스인데 반응이 약하거나 음수였던 종목은 무엇이 달랐는가?
3. 같은 종목에 여러 뉴스가 있었을 때 독립 원인 귀속이 가능한가, 아니면 issuer-day 복합사건인가?
4. 어떤 정책·산업·지역·글로벌 뉴스가 실제 전 시장 breadth를 가진 주도섹터를 만들었는가?
5. BLIND에서 예상한 섹터와 결과 뒤 처음 발견한 섹터를 구분했는가?
6. 직접·간접·시장기억 수혜주 중 실제로 어떤 층이 선택됐는가?
7. 같은 테마에서 왜 특정 종목이 대장이 되고 다른 종목은 선택받지 못했는가?
8. 전일 상한가·회전율·거래대금·최근 급등은 연속성과 선반영 중 어느 쪽으로 작동했는가?
9. 실제 verified 상한가 종목 중 cutoff 전에 예측 가능했던 종목은 몇 개인가?
10. INPUT_MISSING·ENTITY_MISSING·THEME_MAP_MISSING·RANKING_MISS·TIMING_IMPOSSIBLE을 분리했는가?
11. corporate action·신규상장으로 가격 라벨이 차단된 종목을 정상 사례와 섞지 않았는가?
12. 각 사후 교정 교훈의 모든 문장이 해당 사건 FACT와 의미상 일치하는가?
13. 같은 ticker가 이미 후보였는데 종목 누락으로 오분류한 사례는 없는가?
14. retrospective theme의 각 종목에 cutoff 이전 관계 provenance가 있는가?
15. 이번 episode가 두뇌에 추가하는 메커니즘과 반례는 무엇인가?
```

────────────────────────────────────────
13. 비거래일 최소 파일
────────────────────────────────────────

비거래일이면 일반 bundle 대신 다음 정보를 가진 실제 Markdown 파일 하나를 생성한다.

```text
schema_version: nslab.deferred_input.v1
artifact_type: deferred_non_trading_day
status: DEFERRED_NON_TRADING_DAY
brain_eligible: false
outcome_research_performed: false
merge_required: false
covered_by_next_trading_day_csv: true
input_file
input_sha256
calendar_date
previous_trade_date
next_trade_date
created_at
reason
```

────────────────────────────────────────
14. 최종 실행 순서
────────────────────────────────────────

아래 순서를 한 단계도 건너뛰지 않는다.

```text
1. 뉴스 CSV Raw 다운로드·전체 파싱
2. 거래일 D 후보 확정
3. research_daily manifest·schema·access JSON 다운로드·검증
4. blind snapshot(P)만 다운로드·해시·행 수·날짜 검증
5. 뉴스 전 행 disposition 완성
6. entity gate 완성
7. atomic fact·blind inference ledger 생성과 독립 entailment 검증
8. 모든 직접 observation 후보 screening 완성
9. 사건·섹터·후보·continuation·pairwise·red-team 완성
10. BLIND 패킷 전체 파일 저장·해시·봉인
11. 봉인 재검증
12. 그 뒤 outcome snapshot(D) 다운로드·검증
13. 전 시장 outcome ledger·winner census 생성
14. post-seal entity resolution
15. issuer-day 집계와 event-level weight 생성
16. 직접뉴스·테마·수혜주·leader·negative control 연구
17. 적격성 결정
18. Brain Delta 생성
19. canonical research graph 최종화·ID registry 재생성
20. deterministic renderer로 BLIND·POSTMORTEM·기계 artifact·draft bundle 생성
21. 독립 semantic auditor로 모든 training-eligible 사후 오류 교훈과 retrospective member edge를 전수 감사
22. validate_nslab_bundle.py로 JSON·JSONL·entity binding·fact entailment·ID·source·rank·report phase·theme hindsight·pair 방향·사후 교훈 의미정합성·동일 ticker 오류 scope·retrospective member cutoff provenance·placeholder·텍스트 완전성 전수 검증
23. critical error가 있으면 critical gate를 낮추지 말고 canonical graph만 수정한 뒤 최대 16회 19번부터 다시 실행
24. validator exit_code == 0이고 critical_error_count == 0이면 bundle_manifest를 실제 artifact hash/count로 갱신
25. validation_report와 bundle_manifest 계산값으로 direct_ingest_contract.json 생성·검증
26. direct_brain_ingest_ready == true이고 fatal_blockers == []일 때만 ACCEPT_FULL final bundle 조립
27. final bundle을 다시 추출·재검증하고 draft와 hash 비교
28. 실제 다운로드 가능한 MD 파일 생성
```

조기 종료하지 않는다.

단, 다음은 정상 중단 조건이다.

```text
공식 비거래일
BLIND 패킷 봉인 실패
D outcome이 BLIND 전에 실제 노출됨
research_daily snapshot hash 불일치가 재시도 후에도 지속
```

오염이 아니라 단순 파일 일시 오류라면 재시도·검증 후 가능한 영역을 완료한다. 단순 다운로드·JSON·렌더·검증 오류를 이유로 조기 격리 skeleton을 만들지 않는다. fatal content contamination이 아닌 한 ACCEPT_FULL을 목표로 repair loop를 끝까지 수행한다.


## 14.1 `ACCEPT_FULL` 선언 제한

다음 중 하나라도 true이면 `ACCEPT_FULL`을 금지한다.

```text
semantic_feature_error_count > 0
source_ledger_missing_news_row_count > 0
candidate_screening_unlinked_to_fact_or_rejection_count > 0
final_unsupported_mechanism_count > 0
outcome_to_news_missing_audit_count > 0
outcome_to_news_hallucinated_catalyst_count > 0
validation_check_object_schema_error_count > 0
fact_quote_not_found_count > 0
cross_issuer_feature_leak_count > 0
cross_event_feature_leak_count > 0
orphan_reference_count > 0
report_phase_leak_count > 0
watchlist_rank_gap_count > 0
wrong_direction_training_label_count > 0
postseal_winner_used_to_upgrade_blind_theme_count > 0
correction_clause_without_support_count > 0
unsupported_domain_concept_in_correction_count > 0
generic_correction_reuse_across_signature_count > 0
semantic_audit_fail_count > 0
issuer_ranking_miss_for_ticker_present_in_final_watchlist_count > 0
same_ticker_other_event_not_reclassified_to_event_thesis_miss_count > 0
retrospective_theme_member_edge_missing_source_count > 0
retrospective_theme_member_edge_time_unverified_count > 0
retrospective_theme_member_edge_after_cutoff_marked_eligible_count > 0
retrospective_theme_outcome_only_member_marked_eligible_count > 0
retrospective_theme_relation_not_entailed_count > 0
retrospective_theme_semantic_edge_audit_fail_count > 0
semantic_type_fixture_failure_count > 0
retrospective_provenance_fixture_failure_count > 0
error_scope_fixture_failure_count > 0
incomplete_training_text_count > 0
placeholder_token_count > 0
accepted_issuer_false_positive_count > 0
prefix_or_substring_binding_count > 0
group_brand_venue_product_generic_binding_count > 0
entity_binding_regression_failure_count > 0
canonical_graph_consistency_error_count > 0
validator_exit_code != 0
critical_error_count > 0
```

이 경우 오류를 자동 수리하고 재검증한다. 수리되지 않은 상태에서 `brain_eligible=true`를 선언하지 않는다.

위 항목은 `ACCEPT_PARTIAL`로 우회할 수 없다. 수리 불가하면 `QUARANTINE`이다.

────────────────────────────────────────
15. 최종 채팅 응답
────────────────────────────────────────

연구가 끝나면 채팅 본문에는 설명·요약·표·경고·파일목록을 쓰지 않는다.

실제 다운로드 가능한 Markdown 파일 하나를 생성한 뒤 정확히 아래 한 줄만 남긴다.

```text
파일명: <filename>.md
```
