# Codex Goal: 순차 연구 번들 repair-only

## 목표

`docs/설명서.md`의 Phase A대로 `research/inbox/bundles/raw/`의 연구 MD를 날짜순으로
한 파일씩 repair한다. 손실 없이 닫힌 파일은 `READY_FOR_IMPORT`로 만들고, 원문에
실제로 없는 모집단/근거는 창작하지 않고 `PRESERVED_PARTIAL_NOT_CURRENT_GOLD`로
보존한다. 실제 production 두뇌 import는 하지 않는다. 현재 파일을 닫은 뒤에만
다음 파일로 넘어간다.

기존 `sequential_repair_manifest.jsonl`의 `RERUN_REQUIRED`/semantic/BLIND 판정은 이전
계약의 이력일 뿐 현재 판정으로 재사용하지 않는다. 이미 닫힌 원본은 엔진 해시가
바뀌었다는 이유만으로 자동 재실행하지 않으며, 명시적으로 지정한 파일만 다시
평가한다. 결과는 source SHA와 당시 repair engine digest를 함께 새 v2 순차 manifest에
기록한다.

## 재실행 방지와 파일별 종료

한 번의 목표 실행은 원본 **한 파일만** 다룬다. 같은 파일에서 deterministic
출력을 위해 repair를 두 번 만드는 것은 파일별 검수의 일부이며, 이전에 닫힌
파일을 다시 처리하는 동작이 아니다. 기존 manifest에 유효한 결과가 있는
파일은 정상 resume에서 건너뛴다. 전체 과거 파일이나 완료된 월을 자동으로
재실행하지 않는다.

범용 parser/repair 규칙을 변경한 경우에만 회귀 테스트를 먼저 실행하고, 영향이
확인된 source SHA/date를 명시적으로 `--retry-existing --source-date` 또는
`--source-path`로 재검수한다. 그 외의 이전 결과는 당시 engine digest와 함께
감사 가능한 결과로 유지한다. 월간 read-only 검토는 manifest와 산출물을 읽기만
하며 repair/import/재실행을 하지 않는다.

## 고정 운영 계약

```text
1. 원본 경로·SHA-256·크기·거래일을 봉인한다.
2. 원본은 수정하지 않고 별도 .work/<source_sha>/에 repair한다.
3. wrapper/heading/fence/alias 차이는 범용 parser/normalizer로 처리한다.
4. 원본 brain_delta와 machine artifact를 조용히 버리지 않는다.
5. 별도 independent semantic review를 생성하거나 요구하지 않는다.
6. 후공정에서 BLIND 연구를 다시 판정하지 않는다.
7. 연구를 다시 수행하거나 재연구 목록을 만들지 않는다.
8. 명시적 semantic 모순은 연결된 positive training record만 학습 제외한다.
9. negative/audit/context/counterexample record는 semantic 제외 대상으로 오인하지 않는다.
10. 격리 import/deep audit만 실행하며 production 저장소는 수정하지 않는다.
11. 날짜·종목별 맞춤 패치 없이 같은 구조 계열에 적용되는 범용 규칙만 추가한다.
12. 현재 파일의 repair와 검증을 닫은 뒤에만 다음 날짜로 이동한다.
13. 원본 machine payload가 실제로 없으면 내용을 창작하지 않고
    `PRESERVED_SOURCE_PAYLOAD_ABSENT`로 보존한다. 재연구 요청으로 바꾸지 않는다.
14. repaired record의 downstream 역할을 보존한다. `training_eligible=true`만 positive
    training/claim 근거가 될 수 있고, false record는 삭제하지 않은 채 negative control,
    near miss, boundary, audit context로만 사용되도록 exclusion reason을 명시한다.
15. 원본 case artifact가 `retrospective_only=true`,
    `blind_candidate_generated=false`, `training_eligible=false`이고 명시적
    no-bridge 사유를 가진 채 brain_delta에만 빠져 있으면, case의 ID/fact/source만
    복사한 deterministic zero-weight audit/context record를 만들 수 있다. 이 derived
    record는 provenance closure와 derivation hash를 남기며 ticker/company/outcome/semantic
    판단을 새로 만들거나 학습 record로 승격하지 않는다.
```

## 월간 읽기 전용 brain ingest 검토

한 달의 거래일 파일을 모두 닫은 뒤에는 별도 서브에이전트에게 해당 월의 최신
manifest와 repaired 산출물만 읽기 전용으로 검토시킨다. 이 검토는 semantic reviewer를
새로 만드는 작업도 아니고, 파일을 수정하거나 연구를 다시 수행하는 작업도 아니다.
다음 항목만 확인한다.

```text
- 원본 brain_delta record 수/ID/type/payload가 repaired와 닫혔는가
- isolated import/deep audit에서 dropped/quarantine/missing reference가 0인가
- provenance closure와 training_eligible/sample_weight 규칙이 닫혔는가
- semantic 제외 record가 삭제되지 않고 보존되었는가
- ready_for_import와 quality gate가 manifest의 최신 SHA에 결속되는가
- production 저장소가 변경되지 않았는가
```

월간 검토에서 generic parser/repair 결함이 발견되면 날짜·종목 조건을 추가하지 말고
범용 규칙과 회귀 테스트만 패치한다. 그 규칙의 영향을 받는 파일만 명시적으로 다시
repair하고 검수한 뒤 다음 월로 진행한다. 원본 연구 근거가 부족한 진짜 누락은
창작하지 않고 `PRESERVED_PARTIAL_NOT_CURRENT_GOLD`로 남긴다.

`population_underfill_count`, `population_extra_count`,
`liquidity_policy_underspecified_count`가 source와 repaired에서 동일하고
duplicate/lineage/provenance/import 오류가 없으며 현재 case-artifact 계약만 빠진
경우에는 `LEGACY_CONTRACT_POPULATION_QUARANTINED` 경고로 분리한다. 이 경고는
원본 record를 버리거나 current gold라고 주장하지 않지만, brain의 legacy memory
원료로는 보존할 수 있다. 반대로 `closure_content_mismatch_count`, unresolved
provenance, replacement character, record 손실은 hard blocker로 유지한다.
단, 구형 원본이 `record_provenance_closure_audit.jsonl` 블록 자체를 생략한
경우에는 source/fact/inference를 독립 재계산해 eligible closure가 모두 닫힌
때에만 `LEGACY_CLOSURE_ARTIFACT_ABSENT_RECOMPUTED` 경고로 허용한다. 이 경우
importable legacy일 뿐 strict current-gold는 아니다.

## semantic 제외 계약

명시적으로 모순된 positive semantic 관계와 연결된 record는 삭제하지 않고 다음처럼
학습에서만 제외한다.

```text
training_eligible = false
sample_weight = 0.0
training_exclusion_reason = semantic_contract_failed
semantic_exclusion_relation_ids = [근거 relation ID]
```

나머지 record는 그대로 유지한다. 새로운 semantic 판단, 종목 지식, 수혜 관계, fact,
inference를 창작하지 않는다.

## 파일별 실행 순서

```text
원본 census
→ deterministic repair 2회
→ artifact/record lineage와 모집단 대조
→ semantic record 단위 제외 확인
→ inspect-bundle
→ 격리 import --validate --accept
→ 격리 deep audit 및 임시 저장소 삭제
→ repaired/<연도>/에 결과와 quality gate 확정
→ 순차 manifest 갱신
→ 다음 파일
```

## READY_FOR_IMPORT 기준

```text
원본/repaired SHA 결속
strict UTF-8
unclaimed/conflicting machine artifact 0
원본 record 손실 0
설명되지 않은 transform 0
population underfill/extra 0
raw record == normalized record
import loss 0
missing source/payload reference 0
typed payload 오류 0
provenance/eligibility 오류 0
semantic 모순 positive training eligible record 0
deterministic repair true
격리 import/deep audit true
production 저장소 변경 0
```

`semantic_failure_count`는 기존 artifact의 진단 수치일 뿐 bundle 차단 조건이 아니다.
차단 조건은 그 모순에 연결된 positive record가 여전히 학습 가능 상태인지 여부다.
BLIND 관련 front matter와 access-log 선언도 repair 단계에서 재판정하지 않는다.

## 순차 상태 기록

새 결과는 `research/inbox/bundles/repaired/sequential_repair_manifest.v2.jsonl`에 기록한다.
각 행은 최소한 source/repaired SHA, engine digest, record 수, training eligible 수,
semantic 제외 수, 격리 import/deep audit 결과, final status를 포함한다. v1 이력 때문에
원본을 건너뛰지 않는다.

## 검증

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```

## Single-source operating rule

Normal operation processes one source file per run. A source with an existing
manifest result is not re-run only because the repair engine digest changed;
the prior result remains an auditable historical result. To retry a specific
source after a generic rule change, target it explicitly:

```bash
python -m news_scalping_lab.tools.sequential_repair --source-date 20180201 --retry-existing
```

Use `--max-files 1` for normal repair and do not use `--continue-after-blocker`.
A partial or adapter result is recorded for the current source; retry it only
when a generic rule change or an explicit operator request targets that source.
No prior source is revisited implicitly.

## Monthly read-only brain audit

Monthly review is a separate mandatory read-only phase. After the last source
in a calendar month is processed, a different subagent than the repair
operator must inspect that month's latest
manifest rows, repaired artifacts, lineage, isolated import/deep-audit reports,
and production-store invariants. The reviewer must not repair, import, edit
source code, or rerun completed sources.

Do not start the next calendar month until this reviewer has written its audit
result. The result must state whether each file is usable by the brain ingest
pipeline, and must distinguish an actual repair defect from a source-level
partial result or a checker false positive. An incomplete month is not audited
as if it were complete; finish that month first, then perform one audit pass.

The monthly reviewer checks cross-file brain-ingest logic only: record/type and
provenance closure, eligibility and zero-weight exclusions, duplicate IDs,
record coverage, and whether the per-file gate evidence is usable by the brain
pipeline. A monthly finding is classified as `CHECKER_FALSE_POSITIVE`,
`REPAIR_RULE_DEFECT`, or `SOURCE_PARTIAL`; it does not automatically invalidate
the whole month.

If a legacy source omits `record_provenance_closure_audit.jsonl`, the reviewer
must verify the independently recomputed closure counters. A clean recomputed
closure may remain `IMPORTABLE_LEGACY` with
`LEGACY_CLOSURE_ARTIFACT_ABSENT_RECOMPUTED`; it must not be reported as strict
current-gold unless the closure artifact is actually present.

If a generic rule is changed, run its regression tests and explicitly rerun
only the affected source SHA/date. Never restart a completed month merely to
perform the monthly audit. A source that already has a valid `REPAIRED_PASS`
manifest row is skipped by normal resume; `--retry-existing --source-date`
is the only deliberate per-source retry path.

## 금지

```text
production import
independent semantic review sidecar 생성
후공정 BLIND 재판정
재연구 요청 또는 연구 파기
semantic 문제 하나로 bundle 전체 폐기
record silent drop
날짜·종목·테마 하드코딩
```

## Provenance no-fabrication rule

Repair must never attach a generic routing, blind-snapshot, or outcome marker
as a record's provenance merely because that marker exists in `source_ledger`.
`SRC-GOLD-REFERENCE`, `SRC-BLIND-SNAPSHOT`, and similar bundle-level markers
are not evidence for an individual record. If a record has no real source row
and no closed fact/inference path, preserve its original empty provenance,
set its eligibility/weight according to the normal quarantine rule, and record
the unresolved reason. Adding a placeholder source is an illegal transform.
