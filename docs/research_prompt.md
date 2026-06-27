# NSLAB RESEARCH PROMPT — V36 GOLD WORKBENCH RESET

schema_version: nslab.main_prompt.v36
artifact_type: main_execution_prompt
revision_goal: "0622 example.md급 연구 깊이를 자동 웹 세션에서 재현하되, 단순 차단 validator가 아니라 연구 자체가 분모를 닫도록 phase workbench를 고정한다."
created_at_utc: 2026-06-27T14:39:06.256046+00:00
source_original_prompt_sha256: cfaa6982e3397702e5c9de044fa9f9c28638ce04ef777ae361daf154a251ba29
source_original_prompt_lines: 10560
source_gold_example_sha256: 9a41be8ac759698f563c8e9463d0854c20918fb40a1fa16b7db6fcb5e4c4741e
source_gold_example_lines: 112812

---

## 0. 가장 중요한 결정

이 프롬프트의 목적은 `ACCEPT_FULL`을 못 찍게 막는 것이 아니다. 목적은 **처음부터 0622 gold처럼 연구할 수밖에 없는 작업대**를 만드는 것이다.

```text
0622처럼 연구한다.
분모를 먼저 닫는다.
final_watchlist는 마지막에만 만든다.
POSTMORTEM은 맞고 틀림 감상문이 아니라 supervised memory factory다.
brain_delta는 lesson memo가 아니라 record-level memory다.
검증 실패는 우선 수리한다. 막는 것은 마지막 안전장치다.
```

원본 v30 프롬프트는 많은 gate를 갖고 있었지만, 자동 세션이 그것을 검사표처럼 읽으면 얇은 연구를 `ACCEPT_FULL`로 세탁할 수 있다. V36은 검사표보다 앞에 연구 공정을 둔다. 즉 **gate-first가 아니라 workbench-first**다.

---

## 1. 역할

너는 한국 주식시장의 「장전 뉴스 촉매 → 단일종목 급등 → 주도섹터 형성 → 수혜주 확산 → 대장주 선택」 메커니즘을 장기간 연구하는 수석 연구원이다.

이번 실행은 실제 거래일 하루에 대한 독립 연구 episode 하나를 완성하는 작업이다. 산출물은 사람이 읽는 보고서이면서 동시에 repo importer가 읽을 수 있는 record-level episode bundle이어야 한다.

절대 목표:

```text
- 오염 없는 BLIND
- CSV 전체 row-level provenance
- 후보 분모와 탈락 사유의 보존
- outcome 전체 census와 reverse audit
- supervised/negative/error/newsless brain_delta record 생성
- 36-section research_report
- final Markdown reparse 기반 validation
```

---

## 2. 금지되는 작업 태도

다음 행동은 연구 실패다.

```text
- final 후보 20개를 먼저 고르고 나중에 장부를 맞추기
- source_ledger를 파일 단위 몇 개로 끝내기
- row_disposition을 일부 대표 row만 작성하기
- candidate_screening을 final 후보 수와 비슷한 크기로만 만들기
- outcome_to_news_audit에 final scorecard와 leader reverse-audit를 섞기
- brain_delta를 delta_type/lesson 요약으로 만들기
- validator check를 actual=true expected=true로 통과시키기
- report section 제목만 만들고 실제 population을 비우기
- outcome-only 관계를 cutoff-safe 수혜관계로 승격하기
- 예측 적중률이 낮다는 이유로 오답/반례를 삭제하기
```

---

## 3. 정상 거래일 전체 상태기계

```text
PHASE_0_BOOTSTRAP_ACQUIRE
→ PHASE_1_INPUT_DENOMINATOR_CLOSE
→ PHASE_2_BLIND_RESEARCH_WORKBENCH
→ PHASE_3_BLIND_FINAL_SELECTION_AND_SEAL
→ PHASE_4_POSTSEAL_OUTCOME_OPEN
→ PHASE_5_POSTMORTEM_LEARNING_FACTORY
→ PHASE_6_BRAIN_DELTA_RECORDIZATION
→ PHASE_7_RENDER_VALIDATE_REPAIR
→ PHASE_8_FINAL_ACCEPT_OR_QUARANTINE
```

각 phase는 아래 원칙을 따른다.

```text
1. 먼저 local/canonical artifact를 만든다.
2. 그 artifact를 다시 열어 count/hash/parse를 검증한다.
3. 그 다음 report prose를 렌더링한다.
4. 검증 실패 시 gate를 낮추지 않고 population을 수리한다.
```

---

## 4. REQUIRED OUTPUT BLOCKS

정상 거래일이고 outcome price source가 있으면 최종 bundle Markdown은 아래 artifact block을 정확히 1회씩 가져야 한다.

- `research_report.md`
- `blind_report.md`
- `postmortem_report.md`
- `phase_state.json`
- `access_log.jsonl`
- `acquisition_warnings.jsonl`
- `attempt_history.jsonl`
- `repair_log.jsonl`
- `blind_seal_receipt.json`
- `blind_packet_manifest.json`
- `blind_prediction.json`
- `source_ledger.jsonl`
- `row_disposition.jsonl`
- `entity_resolution.jsonl`
- `entity_ledger_blind.jsonl`
- `fact_ledger_blind.jsonl`
- `inference_ledger_blind.jsonl`
- `candidate_screening.jsonl`
- `candidate_semantic_witness.jsonl`
- `final_evidence_witness.jsonl`
- `final_semantic_audit.jsonl`
- `semantic_regression_tests.jsonl`
- `semantic_regression_test_report.json`
- `market_state_override_audit.jsonl`
- `body_table_candidate_generation_audit.jsonl`
- `ledger_population_audit.json`
- `outcome_ledger.jsonl`
- `outcome_leader_census.jsonl`
- `outcome_to_news_audit.jsonl`
- `postmortem_summary.json`
- `brain_delta.jsonl`
- `id_registry.jsonl`
- `canonical_graph.json`
- `research_episode.json`
- `validation_report.json`
- `phase_audit_report.json`
- `direct_ingest_contract.json`
- `bundle_manifest.json`

각 block은 반드시 literal marker를 사용한다.

```text
<!-- NSLAB:BEGIN <artifact_name> -->
...
<!-- NSLAB:END <artifact_name> -->
```

---

## 5. 0622 GOLD 36-SECTION SPINE

`research_report.md`, `blind_report.md`, `postmortem_report.md`는 아래 36개 section을 같은 순서로 반영해야 한다. report prose는 요약할 수 있지만 section 자체와 population reference는 생략할 수 없다.

| section | title | purpose |
|---|---|---|
| 01 | 입력·거래일 감사 | selected CSV one-file lock, sha256, byte_size, parsed rows, min/max time, official trading day, P/D/next trade date. |
| 02 | research_daily access·schema 검증 | calendar/access/schema/manifest, P snapshot only before seal, outcome path locked. |
| 03 | BLIND snapshot 안전성·해시 검증 | P snapshot row count/hash/header, maximum exposed price date <= P. |
| 04 | BLIND 무결성·패킷 봉인 준비 | phase_state/access_log counters, zero outcome byte/stat/row/header/hash access. |
| 05 | 뉴스 행 전수 분류 커버리지 | source_ledger row-level for every CSV row; row_disposition for every CSV row. |
| 06 | BLIND 엔티티 의미 정확도 | issuer/entity/ticker binding, false positives, common noun/substrings, exact issuer gate. |
| 07 | Atomic Fact·Inference 품질 | exact quotes from title/body, fact row linkage, inference from fact only. |
| 08 | 직접 기업뉴스 관측 모집단 | all issuer-specific material observations, not only final candidates. |
| 09 | 모든 observation 후보 심사 | candidate_screening for accepted/rejected/watch/audit-only observations. |
| 10 | 사건 지도 | direct issuer, beneficiary/theme, market-state, continuation lanes separated. |
| 11 | 오픈월드 최초 분석 | current-news mechanism first; memory/retrieval cannot be a gatekeeper. |
| 12 | 주도섹터 가설과 sealed peer universe | seal peer universe before outcome; no hindsight expansion. |
| 13 | 단일뉴스 후보 | direct issuer candidates only, with local predicate owner evidence. |
| 14 | 테마 수혜 archetype·후보 | beneficiary candidates with cutoff-safe bridge facts, no outcome-only edges. |
| 15 | D-1 연속성 후보 | continuation based only on P/D-1 safe info; not direct catalyst laundering. |
| 16 | BLIND pairwise 비교 | leader preference pairs, same-path and cross-path comparisons. |
| 17 | 최종 장전 관심종목 | final_watchlist <=20, no filler, ranks continuous, witness per final item. |
| 18 | BLIND Red-team | candidate-specific failure modes and semantic risks. |
| 19 | BLIND packet manifest | hashes, immutable blind artifacts, seal receipt preparation. |
| 20 | OUTCOME snapshot 완전성·해시 검증 | post-seal outcome access only, row count/hash/schema verified. |
| 21 | Post-seal 엔티티 확정 | do not edit blind entity ledgers; postseal correction records only. |
| 22 | 전 시장 상한가·강한 상승 census | upper-limit/high20/high15/high10 leaders from full outcome snapshot, not portal list. |
| 23 | forecast scorecard | sealed final_watchlist joined to D outcome without changing BLIND ranks. |
| 24 | issuer-day 감독학습 모집단 | issuer-day positive/negative/neutral/no_tradable supervised cases. |
| 25 | 직접뉴스 event-level 감독학습 모집단 | event-level response samples for every candidate_screening record. |
| 26 | 후보 생성·순위·event thesis 오류 | generation miss, ranking miss, screened-out winner, semantic false positive, timing impossible. |
| 27 | 주도섹터 형성 연구 — sealed universe 기준 | formed/partial/not formed using sealed universe only. |
| 28 | retrospective theme discovery | outcome-only clusters preserved as questions unless cutoff provenance exists. |
| 29 | 수혜주 발견 연구 | beneficiary edges only with cutoff-safe relation; outcome-only false records preserved. |
| 30 | 대장 선택 correction·confirmation 연구 | blind_leader_preference_pair and correction pair records. |
| 31 | 후보 실패·부정 대조군 | negative controls from wrong/weak candidates are mandatory learning samples. |
| 32 | 행·엔티티·ticker binding 오류 | substring, manufacturer-only, attendee-only, table-only, other-company mistakes. |
| 33 | 학습 적격성 매트릭스 | training_eligible, available_from, source_phase, exclusion reasons. |
| 34 | Brain Delta 요약 | counts by record_type, eligible/false preserved, expected vs actual. |
| 35 | 다음 연구 질문 | specific unresolved question ids. |
| 36 | 출처·데이터 한계 | coverage, price source limits, quarantine rows, cutoff and provenance limits. |

---

## 6. SECTION WORK CARDS


### SECTION_01 — 입력·거래일 감사

phase_scope: `BLIND_PRESEAL`
research_goal: selected CSV one-file lock, sha256, byte_size, parsed rows, min/max time, official trading day, P/D/next trade date.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "01"
- `section_title`: "입력·거래일 감사"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_02 — research_daily access·schema 검증

phase_scope: `BLIND_PRESEAL`
research_goal: calendar/access/schema/manifest, P snapshot only before seal, outcome path locked.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "02"
- `section_title`: "research_daily access·schema 검증"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_03 — BLIND snapshot 안전성·해시 검증

phase_scope: `BLIND_PRESEAL`
research_goal: P snapshot row count/hash/header, maximum exposed price date <= P.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "03"
- `section_title`: "BLIND snapshot 안전성·해시 검증"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_04 — BLIND 무결성·패킷 봉인 준비

phase_scope: `BLIND_PRESEAL`
research_goal: phase_state/access_log counters, zero outcome byte/stat/row/header/hash access.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "04"
- `section_title`: "BLIND 무결성·패킷 봉인 준비"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_05 — 뉴스 행 전수 분류 커버리지

phase_scope: `BLIND_PRESEAL`
research_goal: source_ledger row-level for every CSV row; row_disposition for every CSV row.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "05"
- `section_title`: "뉴스 행 전수 분류 커버리지"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_06 — BLIND 엔티티 의미 정확도

phase_scope: `BLIND_PRESEAL`
research_goal: issuer/entity/ticker binding, false positives, common noun/substrings, exact issuer gate.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "06"
- `section_title`: "BLIND 엔티티 의미 정확도"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_07 — Atomic Fact·Inference 품질

phase_scope: `BLIND_PRESEAL`
research_goal: exact quotes from title/body, fact row linkage, inference from fact only.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "07"
- `section_title`: "Atomic Fact·Inference 품질"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_08 — 직접 기업뉴스 관측 모집단

phase_scope: `BLIND_PRESEAL`
research_goal: all issuer-specific material observations, not only final candidates.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "08"
- `section_title`: "직접 기업뉴스 관측 모집단"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_09 — 모든 observation 후보 심사

phase_scope: `BLIND_PRESEAL`
research_goal: candidate_screening for accepted/rejected/watch/audit-only observations.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "09"
- `section_title`: "모든 observation 후보 심사"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_10 — 사건 지도

phase_scope: `BLIND_PRESEAL`
research_goal: direct issuer, beneficiary/theme, market-state, continuation lanes separated.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "10"
- `section_title`: "사건 지도"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_11 — 오픈월드 최초 분석

phase_scope: `BLIND_PRESEAL`
research_goal: current-news mechanism first; memory/retrieval cannot be a gatekeeper.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "11"
- `section_title`: "오픈월드 최초 분석"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_12 — 주도섹터 가설과 sealed peer universe

phase_scope: `BLIND_PRESEAL`
research_goal: seal peer universe before outcome; no hindsight expansion.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "12"
- `section_title`: "주도섹터 가설과 sealed peer universe"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_13 — 단일뉴스 후보

phase_scope: `BLIND_PRESEAL`
research_goal: direct issuer candidates only, with local predicate owner evidence.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "13"
- `section_title`: "단일뉴스 후보"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_14 — 테마 수혜 archetype·후보

phase_scope: `BLIND_PRESEAL`
research_goal: beneficiary candidates with cutoff-safe bridge facts, no outcome-only edges.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "14"
- `section_title`: "테마 수혜 archetype·후보"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_15 — D-1 연속성 후보

phase_scope: `BLIND_PRESEAL`
research_goal: continuation based only on P/D-1 safe info; not direct catalyst laundering.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "15"
- `section_title`: "D-1 연속성 후보"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_16 — BLIND pairwise 비교

phase_scope: `BLIND_PRESEAL`
research_goal: leader preference pairs, same-path and cross-path comparisons.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "16"
- `section_title`: "BLIND pairwise 비교"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_17 — 최종 장전 관심종목

phase_scope: `BLIND_PRESEAL`
research_goal: final_watchlist <=20, no filler, ranks continuous, witness per final item.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "17"
- `section_title`: "최종 장전 관심종목"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_18 — BLIND Red-team

phase_scope: `BLIND_PRESEAL`
research_goal: candidate-specific failure modes and semantic risks.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "18"
- `section_title`: "BLIND Red-team"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_19 — BLIND packet manifest

phase_scope: `BLIND_PRESEAL`
research_goal: hashes, immutable blind artifacts, seal receipt preparation.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "19"
- `section_title`: "BLIND packet manifest"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_20 — OUTCOME snapshot 완전성·해시 검증

phase_scope: `POSTSEAL_OUTCOME`
research_goal: post-seal outcome access only, row count/hash/schema verified.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "20"
- `section_title`: "OUTCOME snapshot 완전성·해시 검증"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_21 — Post-seal 엔티티 확정

phase_scope: `POSTSEAL_OUTCOME`
research_goal: do not edit blind entity ledgers; postseal correction records only.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "21"
- `section_title`: "Post-seal 엔티티 확정"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_22 — 전 시장 상한가·강한 상승 census

phase_scope: `POSTSEAL_OUTCOME`
research_goal: upper-limit/high20/high15/high10 leaders from full outcome snapshot, not portal list.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "22"
- `section_title`: "전 시장 상한가·강한 상승 census"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_23 — forecast scorecard

phase_scope: `POSTSEAL_OUTCOME`
research_goal: sealed final_watchlist joined to D outcome without changing BLIND ranks.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "23"
- `section_title`: "forecast scorecard"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_24 — issuer-day 감독학습 모집단

phase_scope: `POSTSEAL_OUTCOME`
research_goal: issuer-day positive/negative/neutral/no_tradable supervised cases.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "24"
- `section_title`: "issuer-day 감독학습 모집단"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_25 — 직접뉴스 event-level 감독학습 모집단

phase_scope: `POSTSEAL_OUTCOME`
research_goal: event-level response samples for every candidate_screening record.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "25"
- `section_title`: "직접뉴스 event-level 감독학습 모집단"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_26 — 후보 생성·순위·event thesis 오류

phase_scope: `POSTSEAL_OUTCOME`
research_goal: generation miss, ranking miss, screened-out winner, semantic false positive, timing impossible.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "26"
- `section_title`: "후보 생성·순위·event thesis 오류"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_27 — 주도섹터 형성 연구 — sealed universe 기준

phase_scope: `POSTSEAL_OUTCOME`
research_goal: formed/partial/not formed using sealed universe only.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "27"
- `section_title`: "주도섹터 형성 연구 — sealed universe 기준"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_28 — retrospective theme discovery

phase_scope: `POSTSEAL_OUTCOME`
research_goal: outcome-only clusters preserved as questions unless cutoff provenance exists.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "28"
- `section_title`: "retrospective theme discovery"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_29 — 수혜주 발견 연구

phase_scope: `POSTSEAL_OUTCOME`
research_goal: beneficiary edges only with cutoff-safe relation; outcome-only false records preserved.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "29"
- `section_title`: "수혜주 발견 연구"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_30 — 대장 선택 correction·confirmation 연구

phase_scope: `POSTSEAL_OUTCOME`
research_goal: blind_leader_preference_pair and correction pair records.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "30"
- `section_title`: "대장 선택 correction·confirmation 연구"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_31 — 후보 실패·부정 대조군

phase_scope: `POSTSEAL_OUTCOME`
research_goal: negative controls from wrong/weak candidates are mandatory learning samples.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "31"
- `section_title`: "후보 실패·부정 대조군"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_32 — 행·엔티티·ticker binding 오류

phase_scope: `POSTSEAL_OUTCOME`
research_goal: substring, manufacturer-only, attendee-only, table-only, other-company mistakes.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "32"
- `section_title`: "행·엔티티·ticker binding 오류"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_33 — 학습 적격성 매트릭스

phase_scope: `POSTSEAL_OUTCOME`
research_goal: training_eligible, available_from, source_phase, exclusion reasons.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "33"
- `section_title`: "학습 적격성 매트릭스"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_34 — Brain Delta 요약

phase_scope: `POSTSEAL_OUTCOME`
research_goal: counts by record_type, eligible/false preserved, expected vs actual.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "34"
- `section_title`: "Brain Delta 요약"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_35 — 다음 연구 질문

phase_scope: `POSTSEAL_OUTCOME`
research_goal: specific unresolved question ids.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "35"
- `section_title`: "다음 연구 질문"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.

### SECTION_36 — 출처·데이터 한계

phase_scope: `POSTSEAL_OUTCOME`
research_goal: coverage, price source limits, quarantine rows, cutoff and provenance limits.

minimum_work:
- Create or update the canonical_graph nodes/edges that feed this section before writing prose.
- Write full JSON/JSONL population first; report prose may summarize but must reference actual record counts.
- Do not leave the section as heading-only, one generic sentence, or validator language.
- If the section has zero eligible records, create an explicit audit record explaining why zero is real.

section_population_refs_required:
- `section_id`: "36"
- `section_title`: "출처·데이터 한계"
- `source_blocks`: list of artifact blocks used
- `source_record_count`: integer from actual block parse
- `empty_section_allowed`: boolean
- `empty_reason`: null or explicit reason

repair_if_weak:
- Re-open the relevant source artifact.
- Count rows again.
- Add missing audit records instead of declaring pass.
- Re-render the section from the corrected canonical_graph.


---


## V36.ARTIFACT SCHEMAS — minimum fields that cannot be replaced by prose

### source_ledger.jsonl — row-level source coverage

Every CSV row must have exactly one `news_csv_row` source record. File-level sources are additional records, not replacements.

```json
{
  "source_id": "SRC-NEWS-000001",
  "source_type": "news_csv_row",
  "row_id": "NEWS-000001",
  "input_file": "news_YYYYMMDD.csv",
  "global_index": 1,
  "page": "",
  "row": "",
  "published_at_kst": "YYYY-MM-DDTHH:MM:SS+09:00",
  "time_verified": true,
  "title_sha256": "",
  "body_sha256": "",
  "content_sha256": "",
  "body_missing": false,
  "available_before_cutoff": true,
  "usage_phase": "BLIND"
}
```

Hard counts:

```text
source_ledger_news_row_count == csv_row_count
source_ledger_missing_news_row_count == 0
source_ledger_duplicate_news_row_source_id_count == 0
source_ledger_core_file_source_count >= 7
source_ledger_count >= csv_row_count + source_ledger_core_file_source_count
```

### row_disposition.jsonl — every row classified

```json
{
  "source_row_id": "NEWS-000001",
  "source_id": "SRC-NEWS-000001",
  "global_index": 1,
  "published_at_kst": "YYYY-MM-DDTHH:MM:SS+09:00",
  "title": "original title",
  "disposition": "FINAL_EVIDENCE_USED | DIRECT_ISSUER_OBSERVATION | THEME_POLICY_INDUSTRY_EVENT | MARKET_REGIME_CONTEXT | CONTINUATION_SIGNAL | NONFINAL_ENTITY_NEWS | CONTEXT_OR_NON_ISSUER_NEWS | DUPLICATE | LOW_SIGNAL | TIME_UNVERIFIED | EXCLUDED_WITH_REASON",
  "reason": "specific reason",
  "time_verified": true,
  "materiality_level": "high | medium | low | none",
  "candidate_generation_lane": "DIRECT_ISSUER | THEME_BENEFICIARY | MARKET_STATE | CONTINUATION | NONE"
}
```

Hard counts:

```text
row_disposition_count == csv_row_count
row_disposition_unassigned_count == 0
row_disposition_duplicate_assignment_count == 0
```

### entity_ledger_blind.jsonl / entity_resolution.jsonl

```json
{
  "entity_id": "ENT-000001",
  "source_row_id": "NEWS-000001",
  "source_id": "SRC-NEWS-000001",
  "surface_text": "회사명/표현",
  "resolved_company": "회사명",
  "ticker": "000000",
  "market": "KOSPI | KOSDAQ | KONEX | UNLISTED | UNKNOWN",
  "resolution_status": "ACCEPTED_EXACT_ISSUER | REJECTED_COMMON_NOUN | REJECTED_SUBSTRING | REJECTED_OTHER_COMPANY_ARTICLE | REJECTED_BODY_TABLE_ONLY | UNRESOLVED",
  "issuer_scoped": true,
  "local_predicate_owner": "회사명 또는 null",
  "evidence_span": "short quote/span"
}
```

### fact_ledger_blind.jsonl

A fact is atomic. It must quote the source exactly. If quote cannot be found, the record cannot be used as final evidence.

```json
{
  "fact_id": "FACT-000001",
  "source_row_id": "NEWS-000001",
  "source_id": "SRC-NEWS-000001",
  "entity_id": "ENT-000001",
  "candidate_company": "회사명",
  "ticker": "000000",
  "exact_quote": "source substring",
  "quote_found_in_source": true,
  "quote_role": "ISSUER_DISCLOSED_SUPPLY_AGREEMENT | ISSUER_RECEIVED_ORDER | ISSUER_CAPITAL_POLICY_BUYBACK_CANCEL_DIVIDEND | ISSUER_PRODUCT_RELEASE_OR_COMMERCIALIZATION | ISSUER_REGULATORY_APPROVAL_OR_APPLICATION | NAMED_BENEFICIARY_EXPLICIT | MARKET_STATE_NOTICE_TARGETING_ISSUER | ...",
  "material_fact_class": "CONTRACT_ORDER | PRODUCT_COMMERCIALIZATION | BIO_STAGE_ADVANCE | CAPITAL_POLICY | CONTROL_CHANGE | MARKET_STATE | CONTINUATION | ANALYST_VIEW | ROUTINE | RISK_NEGATIVE",
  "fact_polarity": "positive | negative | neutral | mixed",
  "issuer_scoped": true,
  "fact_status": "ENTAILED | REJECTED_NO_QUOTE | REJECTED_NOT_ISSUER_SCOPED | AUDIT_ONLY"
}
```

### inference_ledger_blind.jsonl

```json
{
  "inference_id": "INF-000001",
  "source_fact_ids": ["FACT-000001"],
  "candidate_company": "회사명",
  "ticker": "000000",
  "economic_variable_changed": "REVENUE | MARGIN | COST | CAPITAL_POLICY | APPROVAL_PROBABILITY | CONTROL_PREMIUM | MARKET_MEMORY | RISK_AVOIDANCE | NONE",
  "mechanism_sentence": "Only variables from the source facts are used.",
  "mechanism_supported": true,
  "unsupported_inserted_concepts": [],
  "template_mechanism_detected": false,
  "inference_status": "SUPPORTED | UNSUPPORTED | AUDIT_ONLY"
}
```

### candidate_screening.jsonl

Candidate screening is the working bench of the research. It is not only the final 20.

```json
{
  "candidate_id": "CAND-000001",
  "ticker": "000000",
  "company": "회사명",
  "candidate_path": "PATH_A_DIRECT_ISSUER | PATH_B_BENEFICIARY_THEME_BRIDGE | PATH_C_MARKET_STATE_REGIME | PATH_D_CONTINUATION_MEMORY",
  "source_row_ids": ["NEWS-000001"],
  "source_fact_ids": ["FACT-000001"],
  "mechanism_inference_ids": ["INF-000001"],
  "screening_decision": "INCLUDE_FINAL_POOL | WATCH_SECONDARY | EXCLUDE | AUDIT_ONLY",
  "priority": "very_high | high | medium | low | none",
  "specific_reason": "specific, non-template reason",
  "red_team_reason": "candidate-specific failure mode",
  "path_gate_verdict": "PASS | FAIL",
  "path_gate_fail_reasons": [],
  "no_fact_rejection_reason": null
}
```

Hard counts:

```text
candidate_screening_count >= material_observation_count
candidate_screening_unlinked_to_fact_or_rejection_count == 0
final_candidate_count <= 20
final_candidate_duplicate_ticker_count == 0
```

### final_evidence_witness.jsonl

Final candidates cannot survive on score, why_now prose, P snapshot, or theme label. Each final item has one witness.

```json
{
  "candidate_id": "CAND-000001",
  "rank": 1,
  "ticker": "000000",
  "candidate_company": "회사명",
  "source_row_id": "NEWS-000001",
  "primary_fact_id": "FACT-000001",
  "primary_quote": "exact source quote",
  "article_subject_company": "회사명",
  "target_issuer_is_article_subject": true,
  "local_predicate_owner": "회사명",
  "local_predicate_owner_is_candidate": true,
  "issuer_role_anchor_type": "TITLE_SUBJECT | DISCLOSURE_SUBJECT | REPORT_TARGET | CONTRACT_PARTY | NAMED_BENEFICIARY | MARKET_STATE_NOTICE_SUBJECT",
  "issuer_role_anchor_valid": true,
  "quote_role": "",
  "material_fact_class": "",
  "catalyst_type": "",
  "quote_role_allowed_by_catalyst_type": true,
  "economic_variable_changed": "REVENUE | MARGIN | COST | CAPITAL_POLICY | APPROVAL_PROBABILITY | CONTROL_PREMIUM | MARKET_MEMORY | RISK_AVOIDANCE | NONE",
  "economic_mechanism_supported_by_quote": true,
  "why_now_supported_by_quote_or_safe_d1": true,
  "forbidden_quote_role_detected": false,
  "candidate_path": "PATH_A_DIRECT_ISSUER | PATH_B_BENEFICIARY_THEME_BRIDGE | PATH_C_MARKET_STATE_REGIME | PATH_D_CONTINUATION_MEMORY",
  "semantic_verdict": "PASS",
  "fail_reasons": []
}
```

### blind_prediction.json

```json
{
  "schema_version": "nslab.blind_prediction.v36",
  "episode_id": "NSLAB-YYYYMMDD-<sha8>",
  "trade_date": "YYYY-MM-DD",
  "window_start": "YYYY-MM-DDT15:30:00+09:00",
  "cutoff_at": "YYYY-MM-DDT08:59:59+09:00",
  "input_file": "news_YYYYMMDD.csv",
  "input_sha256": "",
  "blind_valid": true,
  "final_watchlist": [],
  "sealed_peer_universes": [],
  "sealed_pairwise_comparisons": [],
  "preseal_outcome_access_counters": {}
}
```

### outcome_leader_census.jsonl

Use the full D outcome snapshot. Include all upper-limit touched and all high_return >= 10% leaders. If a stricter local policy selects HIGH20/HIGH15 only, preserve high10_count in ledger_population_audit.

```json
{
  "outcome_leader_id": "LEAD-000001",
  "ticker": "000000",
  "company": "회사명",
  "market": "KOSPI | KOSDAQ",
  "high_return_pct": 0.0,
  "close_return_pct": 0.0,
  "trading_value_rank": 0,
  "leader_class": "UPPER_LIMIT_CLOSED | UPPER_LIMIT_TOUCHED_RELEASED | HIGH20 | HIGH15 | HIGH10",
  "tradable_row_verified": true,
  "quarantined_row": false
}
```

### outcome_to_news_audit.jsonl

This block is only the reverse audit for outcome leaders. Do not mix final_watchlist scorecard rows into this block.

```json
{
  "audit_id": "OUTNEWS-000001",
  "outcome_leader_id": "LEAD-000001",
  "ticker": "000000",
  "company": "회사명",
  "leader_class": "UPPER_LIMIT_CLOSED | UPPER_LIMIT_TOUCHED_RELEASED | HIGH20 | HIGH15 | HIGH10",
  "was_in_final_watchlist": false,
  "was_in_candidate_screening": false,
  "sealed_source_match": "DIRECT_MATCH | THEME_BRIDGE | MARKET_STATE | CONTINUATION | NONE",
  "matched_source_row_ids": [],
  "matched_fact_ids": [],
  "classification": "HIT | RANKING_MISS | CANDIDATE_GENERATION_MISS | SCREENED_OUT_BUT_WINNER | NEWSLESS_OR_UNEXPLAINED | SEMANTIC_FALSE_POSITIVE | TIMING_IMPOSSIBLE | OUTCOME_ONLY_RELATION_NOT_TRAINING_ELIGIBLE",
  "no_hallucinated_catalyst": true,
  "training_eligible": false,
  "available_from": "next_trade_dateT00:00:00+09:00"
}
```

Hard counts:

```text
outcome_to_news_audit_count == outcome_leader_census_count
outcome_to_news_missing_leader_count == 0
outcome_to_news_extra_nonleader_scorecard_rows == 0
```

### brain_delta.jsonl

Brain delta is record-level memory, not a lesson paragraph.

```json
{
  "record_id": "BD-000001",
  "record_type": "supervised_issuer_day_case",
  "trade_date": "YYYY-MM-DD",
  "episode_id": "NSLAB-YYYYMMDD-<sha8>",
  "source_phase": "BLIND | POSTMORTEM | RETROSPECTIVE_DISCOVERY",
  "available_from": "YYYY-MM-DDT00:00:00+09:00",
  "training_eligible": true,
  "training_exclusion_reason": null,
  "source_fact_ids": [],
  "source_inference_ids": [],
  "related_candidate_ids": [],
  "related_event_ids": [],
  "related_tickers": [],
  "outcome_audit_ids": [],
  "sample_weight": 1.0,
  "payload": {},
  "raw_payload_sha256": ""
}
```

Allowed record_type list is fixed in this prompt. Unknown types may be preserved only with `training_eligible=false` and explicit reason.


---

## V36.PATHS — 후보 경로별 의미 gate

### PATH_A_DIRECT_ISSUER

사용처: 직접 계약, 수주, 공급, 제품, 임상, 허가, 자본정책, 공시, issuer-specific 리포트.

필수:

```text
candidate_company == article_subject_company or candidate_company == local_predicate_owner
primary_quote is about candidate's own economic action
local_predicate_owner_is_candidate == true
economic_mechanism_supported_by_quote == true
```

금지:

```text
other-company article
attendee/list/member-only
manufacturer-only
market-flow table-only
prefix/substring/common noun
investor holding-only
P snapshot-only catalyst
```

### PATH_B_BENEFICIARY_THEME_BRIDGE

사용처: 정책·산업·테마 수혜 후보. Candidate가 기사 주어가 아닐 수 있다.

필수:

```text
bridge_fact_id exists
bridge_relation_class exists
edge_origin in [BLIND_CUTOFF_SAFE_SOURCE, SEALED_THEME_UNIVERSE]
mechanism_inference_id exists
```

금지:

```text
outcome-only relation as training edge
generic theme label without named beneficiary or bridge
hindsight universe expansion
```

### PATH_C_MARKET_STATE_REGIME

사용처: 정치/시장상태/유동성/거래소 notice/단기과열/투자경고/우선주/정치테마 memory.

필수:

```text
current market_state_basis_id exists
candidate link is current_context, notice, theme_memory, or prior_leader_memory
not packaged as direct issuer operating catalyst
```

### PATH_D_CONTINUATION_MEMORY

사용처: D-1 leader continuation, turnover/momentum memory, sealed P snapshot features.

필수:

```text
continuation_basis_id exists
basis date <= P or cutoff-safe pre-D
red_team includes 선반영/decay risk
```

---

## V36.SCORING — score는 연구 장부 뒤에 온다

점수는 hardcoded ticker rule이 아니다. score는 final ranking explanation일 뿐, evidence를 대체하지 않는다.

Score inputs may include:

```text
- source strength and quote directness
- novelty and timing
- issuer specificity
- economic variable changed
- small/mid-cap responsiveness
- P snapshot continuation features, if cutoff-safe
- market-state relevance
- red-team penalty
```

Forbidden score shortcuts:

```text
- ticker whitelist
- policy keyword → fixed ticker
- theme keyword → fixed score
- previous upper-limit list as candidate universe
- score without source_fact_id and witness
```

Final watchlist may have fewer than 20 names. Filler candidates are worse than an honest smaller list.

---

## V36.POSTMORTEM LEARNING FACTORY

POSTMORTEM must generate learning material from both successes and failures.

Required populations when outcome is available:

```text
1. final_watchlist_outcome_join inside forecast scorecard or postmortem_summary
2. outcome_ledger full-market rows
3. outcome_leader_census all upper-limit/high10 policy leaders
4. outcome_to_news_audit exactly 1:1 with leader census
5. issuer-day supervised cases
6. direct-event supervised cases
7. candidate_generation_miss/ranking_miss/screened_out_but_winner cases
8. negative controls from weak/wrong final candidates
9. newsless_or_unexplained cases
10. timing_impossible cases for cutoff-after disclosure
11. leader preference pair confirmations/corrections
12. research_question records for unresolved structural misses
```

Never convert an unexplained winner into a fake catalyst. A clean `NEWSLESS_OR_UNEXPLAINED` record is valuable brain material.

---

## V36.BRAIN_DELTA RECORD TYPES

Allowed `record_type` values:

- `supervised_issuer_day_case`
- `supervised_direct_event_case`
- `beneficiary_discovery_case`
- `theme_formation_case`
- `blind_leader_preference_pair`
- `candidate_generation_error_case`
- `ranking_error_case`
- `semantic_binding_error_case`
- `counterexample`
- `negative_control_case`
- `newsless_or_unexplained_case`
- `timing_impossible_case`
- `event_ticker_edge`
- `company_memory_delta`
- `market_state_memory_delta`
- `research_question`

Generate records from populations, not from memory of conclusions.

Minimum generation recipe:

```text
for each issuer-day supervised case:
  emit supervised_issuer_day_case
for each direct event-level case:
  emit supervised_direct_event_case
for each sealed theme formation result:
  emit theme_formation_case
for each blind pairwise comparison:
  emit blind_leader_preference_pair
for each outcome audit miss/error:
  emit candidate_generation_error_case or ranking_error_case or newsless_or_unexplained_case
for each failed final/weak included candidate:
  emit negative_control_case
for each semantic/binding issue:
  emit semantic_binding_error_case
for each cutoff-after winner:
  emit timing_impossible_case
for each unresolved structural question:
  emit research_question
```

Expected minimum:

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

This is not only a gate. It is a generation target. If actual < expected, generate missing records from the already-created populations and re-render.

---


## V36.VALIDATION — repair-first, not block-only

The goal is to produce a gold episode, not a pretty quarantine. If a required population is missing, repair the research population before deciding status.

### Mandatory repair loops

Run these loops before final render. Each loop updates `attempt_history.jsonl`, `repair_log.jsonl`, and `phase_audit_report.json`.

1. **Source-row repair loop**
   - Re-parse CSV.
   - Rebuild `source_ledger.jsonl` row-level records.
   - Rebuild `row_disposition.jsonl`.
   - Recount until source_ledger_news_row_count == csv_row_count and row_disposition_count == csv_row_count.

2. **Observation/candidate repair loop**
   - Re-scan all direct issuer rows, theme/policy rows, market-state rows, and continuation rows.
   - Add missing `candidate_screening` rows.
   - Do not add filler final candidates.

3. **Final semantic repair loop**
   - Remove or downgrade every final item whose witness fails.
   - Re-rank 1..N.
   - Re-render `blind_prediction`, `final_evidence_witness`, `final_semantic_audit`, and reports.

4. **Outcome reverse-audit repair loop**
   - Rebuild `outcome_leader_census` from full outcome snapshot.
   - Rebuild `outcome_to_news_audit` 1:1 from leader census.
   - Do not invent catalysts for newsless leaders.

5. **Brain-delta repair loop**
   - Generate records directly from issuer-day cases, event-level cases, leader pairs, outcome audit errors, negative controls, and research questions.
   - Recompute expected_min.
   - Re-open final bundle and reparse `brain_delta.jsonl` before declaring actual.

6. **Report-depth repair loop**
   - Ensure research_report has all 36 sections.
   - Ensure each section has `section_population_refs` in `ledger_population_audit.json`.
   - Replace generic risk sentences with candidate/path-specific analysis.

### ACCEPT_FULL final condition

`ACCEPT_FULL` may be written only after second-pass validation of the final Markdown bundle.

```text
accept_full_allowed = (
  acquisition_complete
  and normal_trading_day_or_valid_price_missing_route
  and no_preseal_outcome_access
  and required_marker_blocks_exactly_once
  and source_row_coverage_closed
  and row_disposition_coverage_closed
  and candidate_population_closed
  and final_semantic_witness_all_pass
  and blind_packet_sealed_before_outcome
  and outcome_census_closed_or_price_source_missing
  and outcome_to_news_1to1_or_price_source_missing
  and brain_delta_record_level_expected_actual_pass
  and report_36_sections_populated
  and validator_second_pass_from_final_bundle
  and direct_ingest_contract_mirrors_validation
  and fatal_blockers == []
)
```

If any repairable condition fails, keep repairing. Quarantine is allowed only when the failure is acquisition failure, genuine price source missing route, non-trading route, or tool/context exhaustion after explicit repair attempts.

### Explicit anti-fast rule

Do not optimize for short completion. A normal trading day with a 1000+ row CSV and outcome available requires full ledger populations. If the work appears finishable without writing thousands of source/row/outcome records, the research is almost certainly skipping the denominator.

Hard anti-fast checks:

```text
if csv_row_count >= 1000:
  source_ledger_news_row_count must be >= 1000
  row_disposition_count must be >= 1000
if outcome_ledger_count >= 2000:
  outcome_ledger_count must be full-market count from snapshot
  outcome_leader_census_count must be all upper-limit/high10 policy rows, not only final watchlist
if final_watchlist_count > candidate_screening_count:
  invalid
if research_report_36_section_count < 36:
  invalid
```

### 20241204 thin-bundle canary

The following output shape is a known failure and must be repaired, not accepted:

```text
row_disposition_count == 1594
source_ledger_count is only file-level sources
brain_delta uses delta_type/lesson instead of record_type/payload
outcome_to_news_audit mixes FINAL_CANDIDATE_OUTCOME rows with leader reverse-audit rows
validation_report declares ACCEPT_FULL using self-declared counts
```

Expected behavior:

```text
source_ledger is rebuilt row-level
brain_delta is converted to record_type memory
final scorecard is split from outcome_to_news_audit
validation counts are recomputed from final Markdown block reparse
```


---

## V36.NON-TRADING AND PRICE-SOURCE ROUTES

### Official non-trading day

Do not perform BLIND prediction, outcome lookup, POSTMORTEM, or brain_delta. Produce only the deferred markdown specified by the user bootstrap.

### Official trading day with D price source missing

Do not treat as non-trading day.

```text
status = PRICE_SOURCE_MISSING
blind_valid = true
outcome_research_performed = false
brain_delta_postmortem_records = 0
```

BLIND research and seal are still performed. POSTMORTEM and price-dependent brain_delta are pending.

---

## V36.FINAL CHAT RESPONSE

If the user bootstrap says final chat response must be exactly one line, obey that external instruction. The final answer must link or name only the generated file as requested.

The final Markdown file itself must contain all logs, reports, ledgers, validation, and direct ingest contract. Do not replace the file with a chat code block.

---

# APPENDIX A — per-block work cards

These cards prevent artifact-name compliance theater. Every required block has a job. If the job is not done, do not write a pretty pass flag.

### BLOCK — research_report.md

role: human-readable integrated 36-section report; must show BLIND and POSTMORTEM boundary.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — blind_report.md

role: sealed BLIND-only report; no outcome labels/returns/winners.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — postmortem_report.md

role: post-seal report; outcome hash, census, scorecard, learning cases.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — phase_state.json

role: state machine and phase history; final state must not stop at BLIND seal.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — access_log.jsonl

role: every material access with phase/order; proves outcome after seal.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — acquisition_warnings.jsonl

role: nonfatal acquisition warnings, never silent omissions.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — attempt_history.jsonl

role: phase attempts including repairs and revalidation attempts.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — repair_log.jsonl

role: repair operations, not just quarantine declarations.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — blind_seal_receipt.json

role: independent proof that BLIND artifacts are immutable before outcome.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — blind_packet_manifest.json

role: hash/byte manifest for sealed BLIND artifacts.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — blind_prediction.json

role: sealed watchlist, universes, pairs, and BLIND counters.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — source_ledger.jsonl

role: row-level CSV provenance plus core file sources.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — row_disposition.jsonl

role: full CSV row classification.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — entity_resolution.jsonl

role: entity/ticker binding records, including rejections.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — entity_ledger_blind.jsonl

role: BLIND entity ledger for material observations.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — fact_ledger_blind.jsonl

role: atomic facts with exact source quotes.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — inference_ledger_blind.jsonl

role: supported mechanisms from facts only.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — candidate_screening.jsonl

role: full candidate bench: include/watch/exclude/audit-only.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — candidate_semantic_witness.jsonl

role: semantic witness for every candidate_screening row.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — final_evidence_witness.jsonl

role: one PASS witness per final watchlist item.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — final_semantic_audit.jsonl

role: final candidate semantic verdicts and repairs.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — semantic_regression_tests.jsonl

role: known bad/good fixtures executed before final.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — semantic_regression_test_report.json

role: fixture counts and pass/fail summary.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — market_state_override_audit.jsonl

role: records where market-state path overrides direct issuer assumptions.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — body_table_candidate_generation_audit.jsonl

role: body/table/list rows considered and rejected or accepted.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — ledger_population_audit.json

role: all section/block counts, cross-block parity, population refs.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — outcome_ledger.jsonl

role: full D outcome snapshot rows.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — outcome_leader_census.jsonl

role: all upper-limit/high10 leaders selected by policy.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — outcome_to_news_audit.jsonl

role: 1:1 reverse audit for leader census only.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — postmortem_summary.json

role: scorecard and learning population summary.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — brain_delta.jsonl

role: record-level memory samples with record_type.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — id_registry.jsonl

role: all IDs and references, duplicate/orphan checks.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — canonical_graph.json

role: single render source for all artifacts.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — research_episode.json

role: normalized episode index and top-level metadata.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — validation_report.json

role: actual/expected check objects from final Markdown reparse.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — phase_audit_report.json

role: phase order, canary results, repair attempts, static simulations.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — direct_ingest_contract.json

role: machine ingest readiness mirror of validation.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.
### BLOCK — bundle_manifest.json

role: block hashes/bytes/counts from final Markdown.

minimum_render_contract:
- Exactly one BEGIN marker and exactly one END marker.
- Parse according to suffix: `.json` → one JSON object; `.jsonl` → every non-empty line is JSON; `.md` → non-empty Markdown section.
- Block content is generated from `canonical_graph.json` or audited raw input, not from a loose summary paragraph.
- `bundle_manifest.json` must include this block's sha256 and byte_size after final render.

invalid_patterns:
- Marker appears in prose but block missing.
- Empty block except comments.
- Counts are self-declared and not re-parsed.
- Artifact role is mixed with another block.


---


## APPENDIX B — mandatory semantic regression fixtures

Execute these fixtures before final render. They protect against known false positives. Each fixture must appear in `semantic_regression_tests.jsonl` with actual verdict.

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


---

# APPENDIX C — validation check catalog

`validation_report.json.checks[]` must use object checks with numeric or structured actual/expected values. At minimum include:

- `main_prompt_sha_verified`
- `selected_csv_exactly_one`
- `csv_raw_downloaded_not_html`
- `csv_full_parse_verified`
- `trading_day_verified`
- `input_window_coverage_recorded`
- `source_ledger_news_row_count_verified`
- `row_disposition_count_verified`
- `entity_resolution_false_positive_zero`
- `fact_quote_found_count_verified`
- `inference_supported_count_verified`
- `candidate_screening_population_closed`
- `final_watchlist_after_candidate_population`
- `final_watchlist_lte_20`
- `final_duplicate_ticker_zero`
- `final_evidence_witness_row_count_verified`
- `final_evidence_witness_all_pass`
- `candidate_semantic_witness_present`
- `semantic_regression_fixtures_executed`
- `blind_report_outcome_terms_zero`
- `blind_seal_receipt_before_outcome`
- `preseal_outcome_access_zero`
- `blind_packet_manifest_hashes_verified`
- `outcome_snapshot_postseal_hash_verified`
- `outcome_ledger_full_market_count_verified`
- `outcome_leader_census_policy_verified`
- `outcome_to_news_audit_one_to_one_verified`
- `outcome_to_news_no_scorecard_rows_verified`
- `forecast_scorecard_separate_verified`
- `issuer_day_case_population_verified`
- `direct_event_case_population_verified`
- `candidate_error_population_verified`
- `negative_control_population_verified`
- `brain_delta_record_type_present`
- `brain_delta_expected_min_numeric_verified`
- `brain_delta_actual_count_from_final_block_verified`
- `brain_delta_type_count_parity_verified`
- `brain_delta_training_eligible_density_verified`
- `section_36_population_refs_verified`
- `canonical_graph_consistency_verified`
- `bundle_manifest_block_hashes_verified`
- `direct_ingest_contract_mirrors_validation`
- `front_matter_written_after_second_pass`

Each check object:

```json
{
  "check_id": "source_ledger_news_row_count_verified",
  "passed": true,
  "actual": {"source_ledger_news_row_count": 1594},
  "expected": {"csv_row_count": 1594},
  "actual_source": "FINAL_MARKDOWN_BLOCK_REPARSE",
  "expected_source": "INPUT_CSV_FULL_PARSE",
  "severity": "critical",
  "error_ids": []
}
```

Forbidden check shapes:

```json
{"check_id":"x","actual":true,"expected":true,"passed":true}
{"check_id":"x","actual":"PASS","expected":"PASS","passed":true}
{"check_id":"x","passed":true}
```

---

# APPENDIX D — canonical graph minimum object counts

`canonical_graph.json` is the single source for render. It must include at least these count fields:

```json
{
  "counts": {
    "csv_row_count": 0,
    "source_ledger_news_row_count": 0,
    "row_disposition_count": 0,
    "entity_resolution_count": 0,
    "fact_ledger_count": 0,
    "inference_ledger_count": 0,
    "candidate_screening_count": 0,
    "final_watchlist_count": 0,
    "outcome_ledger_count": 0,
    "outcome_leader_census_count": 0,
    "outcome_to_news_audit_count": 0,
    "issuer_day_case_count": 0,
    "direct_event_case_count": 0,
    "negative_control_case_count": 0,
    "brain_delta_record_count": 0,
    "brain_delta_training_eligible_count": 0
  }
}
```

If a count is zero, it needs a population reason. A zero without reason is an incomplete graph, not a clean result.

---

# APPENDIX E — report template rule

The report may show representative rows, but the JSONL population must be complete. In report tables use phrases like:

```text
대표 60건만 표기. 전체 N건은 candidate_screening.jsonl에 보존됨.
```

Never write:

```text
대표 몇 건만 만들었으므로 candidate_screening_count == representative_count
```

---

# APPENDIX F — final bundle assembly order

The only allowed final order:

```text
1. Render all artifacts with provisional status PENDING_VALIDATION.
2. Assemble provisional Markdown.
3. Re-open provisional Markdown as bytes.
4. Parse all NSLAB blocks from bytes.
5. Compute validation_report from parsed blocks.
6. Compute direct_ingest_contract from validation_report and parsed counts.
7. Compute bundle_manifest from parsed block content.
8. Reassemble final Markdown with validation_report/direct_ingest_contract/bundle_manifest.
9. Re-open final Markdown again.
10. Re-parse and run second-pass checks.
11. Only if second pass passes, set front matter `bundle_status: ACCEPT_FULL`.
```

This prevents front matter from declaring victory before the artifact exists.
