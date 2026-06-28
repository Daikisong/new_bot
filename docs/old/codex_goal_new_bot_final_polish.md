# Codex Goal — new_bot 최종 정합성·Gold 연구 두뇌 연결 패치

너는 `Daikisong/new_bot` 저장소의 수석 아키텍트이자 구현 담당자다.

이번 작업은 대규모 재설계가 아니다. 이미 구현된 record-level bundle import, BrainRecordEnvelope, warehouse, llm-full/catalog 분리, production doctor, training export 구조를 유지하면서, `docs/example2.md` 같은 gold 연구 번들이 실제 production 두뇌에 손실 없이 들어가고, 이후 자동 연구가 같은 방향으로 누적되도록 마지막 정합성·회귀·문서·명령 경로를 고정하라.

질문을 되묻지 말고 현재 저장소를 직접 조사한 뒤 구현·테스트·문서 갱신까지 완료하라.

---

## 0. 현재 프로젝트 목적 재확인

이 저장소는 뉴스 키워드 → 고정 종목/섹터 매핑을 만드는 규칙 엔진이 아니다.

목표는 다음이다.

```text
GPT Pro 연구 번들
→ immutable raw 보존
→ version-aware import
→ brain_delta record 손실 없는 정규화
→ record-level warehouse/retrieval/training
→ llm-full category-specific brain compile
→ 새 장전 CSV open-world 분석
→ record-level positive/negative/counterexample/leader/theme 사례 참고
→ 주도섹터·단일뉴스·수혜주·연속성 후보 산출
```

`docs/example2.md` 같은 gold 연구결과가 이 파이프라인의 표준 입력이다.

---

## 1. 절대 유지할 것

아래는 이미 방향이 맞으므로 갈아엎지 마라.

```text
records/models.py의 BrainRecordEnvelope 계층
versioned_bundle.py의 version-aware import 계층
BrainRecordStore의 immutable raw/record manifest/index 구조
record-level warehouse
training export의 explicit brain record 기반 구조
brain compiler의 catalog/llm-full 분리
mock/catalog production 승격 금지
doctor --production gate
hardcoding/lookahead/provenance audit
research_daily P/D 가격 분리
docs/example2.md gold bundle shape
```

수정 목표는 “더 큰 새 아키텍처”가 아니라 **현재 구조가 실제 production 경로에서 흔들리지 않도록 고정**하는 것이다.

---

## 2. 반드시 확인할 현재 상태

현재 저장소를 직접 검사하고 다음을 보고하라.

```text
docs/example2.md
docs/research_prompt.md
docs/session_prompt.md
README.md
AGENTS.md
src/news_scalping_lab/research_import/versioned_bundle.py
src/news_scalping_lab/research_import/bundle.py
src/news_scalping_lab/research_import/importer.py
src/news_scalping_lab/records/models.py
src/news_scalping_lab/records/store.py
src/news_scalping_lab/warehouse.py
src/news_scalping_lab/training.py
src/news_scalping_lab/brain/compiler.py
src/news_scalping_lab/brain/audit.py
src/news_scalping_lab/diagnostics.py
src/news_scalping_lab/cli.py
tests/
```

---

## 3. `research import`와 `research import-bundle` 경로 혼동 제거

현재 production gold bundle 수입 경로는 반드시 version-aware path여야 한다.

### 3.1 문제

`research import-bundle`은 versioned_bundle을 쓰지만, legacy `research import` 또는 `ResearchImporter.import_path(..., mode="auto")`가 Markdown bundle을 만나 old `import_bundle_episode()`로 보내면 v10/v11/v23 rich bundle을 구형 `ResearchEpisode`로 강제 검증/변환할 위험이 있다.

### 3.2 수정

다음 중 하나로 고정하라.

권장 A:

```text
ResearchImporter.import_path_async에서 looks_like_bundle(preserved)이 true이면
반드시 import_versioned_bundle(...)로 delegate한다.
```

또는 권장 B:

```text
legacy `research import`가 v10/v11/v23 bundle을 만나면 즉시 실패하고
"Use research import-bundle for NSLAB research bundles"를 출력한다.
```

어느 쪽이든 다음은 금지한다.

```text
v10/v11/v23 Markdown bundle
→ legacy ResearchEpisode.model_validate
→ extra field drop 또는 schema failure
```

### 3.3 테스트

추가 테스트:

```text
test_research_import_auto_delegates_gold_bundle_to_versioned_import
test_research_import_does_not_force_v11_bundle_into_legacy_episode
test_import_bundle_and_import_auto_have_same_record_count_for_example2
```

---

## 4. `docs/example2.md`를 실제 fixture로 승격

`docs/example2.md`는 문서 샘플이 아니라 gold shape reference다.

### 4.1 복사/링크

다음 중 하나를 구현하라.

```text
tests/fixtures/research_bundles/example2_gold.md
```

또는 테스트에서 `docs/example2.md`를 직접 읽어도 된다.

단, 문서 샘플이라는 이유로 production smoke에서 제외되는 `.example.md`와 다르게 취급해야 한다.

### 4.2 parity 테스트

`docs/example2.md`에 대해 다음을 검사한다.

```text
bundle_schema_version == nslab.research_bundle.v11
bundle_status == ACCEPT_FULL
validator_exit_code == 0
blind_valid == true
brain_delta raw count == normalized count
training_eligible raw count == normalized eligible count
record_id set equality
record_type count equality
raw payload hash parity
source/provenance closure
record manifest count parity
warehouse projection count parity
training export skip/eligible count parity
```

정확한 숫자는 example2 자체에서 계산한다. 특정 날짜 값 327/325를 하드코딩하지 말고, expected value는 bundle manifest와 raw brain_delta에서 읽는다.

### 4.3 회귀 테스트

추가 테스트:

```text
test_example2_gold_bundle_import_loss_zero
test_example2_gold_bundle_record_manifest_matches_raw
test_example2_gold_bundle_warehouse_projection_matches_records
test_example2_gold_bundle_training_export_uses_only_eligible_records
test_example2_gold_bundle_preference_export_uses_only_blind_leader_pairs
```

---

## 5. docs/research_prompt.md와 importer 계약 정합성 고정

현재 `docs/research_prompt.md`는 v11 gold shape와 direct ingest 관련 강한 계약을 담고 있다. importer가 실제로 받아들일 수 없는 필드를 출력하게 만들면 안 된다.

### 5.1 검사

`docs/research_prompt.md`에서 요구하는 다음 필드를 importer/test가 실제로 처리하는지 확인한다.

```text
schema_version: nslab.research_bundle.v11
execution_protocol_version: nslab.brain_grade_semantic_provenance_locked.v11
direct_brain_ingest_ready
direct_ingest_contract.json
validation_report.json
bundle_manifest.json
brain_delta.jsonl
record_import_manifest
canonical_graph_sha256
renderer_version
validator_version
```

### 5.2 version adapter 정합성

다음 중 어떤 경로가 공식인지 명확히 하라.

```text
A. v11 adapter가 docs/research_prompt.md 출력 전체를 직접 import
B. v11 envelope + bundle_manifest.v23 + direct_ingest_contract는 v23-direct-ingest adapter 사용
```

둘 다 지원한다면 문서에 정확히 쓴다.

지원하지 않는 조합이 있다면 `docs/research_prompt.md`가 그 조합을 출력하지 않게 수정한다.

### 5.3 테스트

```text
test_research_prompt_gold_contract_matches_supported_adapter_versions
test_direct_ingest_contract_required_only_when_adapter_supports_it
test_unsupported_direct_ingest_contract_shape_quarantined_without_drop
```

---

## 6. record-level brain coverage를 production gate로 고정

episode coverage와 record coverage를 혼동하지 않는다.

### 6.1 필수 manifest

`brain/current/record_coverage_manifest.json`에는 최소 다음이 있어야 한다.

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
coverage_complete
```

### 6.2 production doctor gate

`doctor --production`은 다음이면 실패해야 한다.

```text
record_coverage_manifest missing
coverage_complete != true
accepted_record_count != compiled_record_count
current compile source record count != current record coverage count
semantic index record count != current record coverage count
```

### 6.3 테스트

```text
test_doctor_production_fails_when_record_coverage_missing
test_doctor_production_fails_when_record_coverage_incomplete
test_brain_compile_source_record_count_matches_record_coverage
```

---

## 7. llm-full production brain이 실제 LLM compile인지 검증

### 7.1 금지

```text
catalog brain
mock LLM brain
all-cache llm-full with zero live call
deterministic embeddings
```

을 production brain으로 인정하지 않는다.

### 7.2 manifest 필수

`brain/current/brain_manifest.json` 또는 compile report에 다음을 기록한다.

```text
build_mode
catalog_only
provider
model
prompt_hash
compile_run_id
live_llm_call_count
cache_hit_count
source_record_count
compiled_claim_count
category_claim_counts
category_source_record_type_counts
```

production readiness는 다음을 요구한다.

```text
build_mode == llm-full
catalog_only == false
live_llm_call_count >= 1
source_record_count == accepted_record_count
```

### 7.3 테스트

```text
test_doctor_production_rejects_catalog_brain
test_doctor_production_rejects_mock_llm_brain
test_doctor_production_rejects_all_cache_llm_full_without_live_call
test_llm_full_manifest_records_provider_model_and_live_call_count
```

---

## 8. 9개 brain 파일 category-specific 보장

현재 compiler가 9개 파일을 만들더라도 category별 내용과 source population이 달라야 한다.

### 8.1 검사

다음 파일을 감사한다.

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

필수:

```text
byte-identical pair 0
제목만 다른 동일본문 0
각 category claim이 해당 record_type/source population과 연결
각 claim에 supporting_record_ids 존재
contradicting_record_ids 또는 boundary_conditions 보존
한 episode만으로 validated status 금지
```

### 8.2 테스트

```text
test_category_brain_files_not_byte_identical
test_category_claims_reference_category_source_records
test_compiled_claims_have_supporting_record_ids
test_single_episode_claims_remain_tentative
```

---

## 9. training export는 explicit record만 사용

### 9.1 금지

```text
blind_predictions 전체에서 임의 positive/negative cross product 생성
postmortem 산문에서 generic lesson 재생성
candidate list 전체를 preference pair로 변환
training_eligible=false record export
```

### 9.2 허용 source

```text
supervised_issuer_day_case
supervised_direct_event_case
supervised_theme_formation_case
blind_leader_preference_pair
명시적으로 eligible인 error/correction records
```

### 9.3 검증

```text
issuer-day sample weight 합 == 1
event-level sample weight 합 == 1
preference는 blind_leader_preference_pair만 사용
BLIND-safe SFT와 POSTMORTEM correction 분리
ineligible skip manifest 작성
```

### 9.4 테스트

```text
test_training_export_sft_uses_explicit_eligible_records
test_training_export_preference_uses_only_blind_leader_pairs
test_training_export_rejects_ineligible_records
test_training_export_validates_issuer_day_weights
test_training_export_separates_blind_and_postmortem_phases
```

---

## 10. docs 정리

현재 `docs/`에는 연구 프롬프트, goal, example, CSV 등이 섞여 있다. 자동화가 잘못된 파일을 읽지 않도록 정리한다.

### 10.1 권장 구조

```text
docs/
  research_prompt.md              # current production prompt
  session_prompt.md               # automation runner prompt
  codex_goal_news_lab_brain_upgrade.md
  example2.md                     # current gold bundle sample
  README.md                       # docs 파일 용도 설명
  archive/
    old_prompts/
    old_examples/
```

이동이 부담되면 최소한 `docs/README.md`에 다음을 명시한다.

```text
current production prompt = docs/research_prompt.md
current gold output shape = docs/example2.md
do not use example.md as gold
do not use archived prompts for production
```

### 10.2 Tampermonkey/session prompt 확인

`docs/session_prompt.md` 또는 자동화 스크립트가 반드시 현재 production prompt를 가리키는지 확인한다.

```text
https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md
```

---

## 11. 실제 실행 smoke

실제 API key가 없으면 production llm-full brain 승격은 하지 말고, 그 사실을 명확히 보고한다.

필수 smoke:

```bash
python -m news_scalping_lab.cli research inspect-bundle docs/example2.md
python -m news_scalping_lab.cli research import-bundle docs/example2.md --validate --accept
python -m news_scalping_lab.cli memory audit --deep
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli warehouse verify
python -m news_scalping_lab.cli training export-sft
python -m news_scalping_lab.cli training export-preference
python -m news_scalping_lab.cli training export-evals
python -m news_scalping_lab.cli training audit
python -m news_scalping_lab.cli brain rebuild --mode catalog --allow-catalog
python -m news_scalping_lab.cli brain audit --deep
python -m news_scalping_lab.cli doctor --production
```

기대:

```text
import/warehouse/training/catalog audit는 통과 가능
doctor --production은 real LLM/embedding/web provider 없으면 실패해야 정상
```

real keys가 있으면 다음까지 수행한다.

```bash
python -m news_scalping_lab.cli brain rebuild --mode llm-full
python -m news_scalping_lab.cli memory rebuild-index --production
python -m news_scalping_lab.cli brain audit --deep
python -m news_scalping_lab.cli doctor --production
```

---

## 12. 최종 보고 방식

최종 응답에는 반드시 다음을 포함한다.

```text
1. docs/example2.md import 결과
2. raw brain_delta count
3. normalized record count
4. training eligible count
5. record type count parity
6. dropped record count
7. quarantined record count
8. warehouse table row counts
9. training export counts
10. brain compiler mode
11. brain category diversity audit
12. record coverage audit
13. production doctor result
14. tests/ruff/mypy 결과
15. 아직 필요한 API key/env
16. 변경한 파일 목록
```

성공을 과장하지 않는다. `doctor --production`이 provider 부재로 실패하면 실패가 맞으며, catalog brain을 production이라고 말하지 않는다.
