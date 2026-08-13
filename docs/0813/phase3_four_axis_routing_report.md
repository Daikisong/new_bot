# Phase 3 네 축 라우팅 분리 보고서

상태: 구현·내부 검증·독립감사 승인 완료

## 1. 목적

`training_eligible`을 호재 여부로 오해하지 않도록 다음 네 축을 독립적으로 계산한다.

```text
evidence_polarity
training_eligible
label_quality
routing_disposition
```

핵심 계약은 다음과 같다.

```text
training_eligible=true
  = 근거·provenance·학습 계약을 만족함
  != 긍정 사례

positive reasoning support
  = polarity=POSITIVE
  + training_eligible=true
  + label_quality=verified
  + routing_disposition=REASONING
```

## 2. 구현

### 2.1 공통 라우터

`records/routing.py`를 단일 해석 지점으로 사용한다.

- `RecordEvidencePolarity`: POSITIVE, NEGATIVE, NEAR_MISS, UNEXPLAINED, CONTEXT, UNKNOWN
- `RecordLabelQuality`: verified, quarantined, no_tradable_row, missing, ambiguous, conflicting, not_applicable
- `RecordRoutingDisposition`: REASONING, CONTEXT, AUDIT, QUARANTINED
- `record_polarity.v2`: eligibility를 polarity 계산에서 제거
- 숫자 fallback threshold는 `retrieval_calibration_only`로 명시
- label과 숫자 결과가 충돌하거나 quality 값이 중첩 충돌하면 AUDIT
- 미등록·부정형·복합충돌 response label과 eligibility mirror 충돌은 fail-closed
- 일반 outcome record는 명시 label만으로 승격하지 않고 usable numeric outcome도 요구
- candidate error는 작업 오류 lane과 실제 가격 polarity를 분리
- unknown typed payload는 보존하되 QUARANTINED

구형 연구 record는 `label_quality` 문자열이 없을 수 있다. 다음 조건을 모두 만족할 때만 기존 outcome 계약을 `verified`로 승계한다.

```text
known typed payload
training_eligible envelope=true
payload training_eligible mirror=true
status=supported
non-empty provenance_source_ids
usable numeric outcome
```

이 조건이 하나라도 없으면 숫자 방향은 보존하지만 reasoning·학습에는 넣지 않는다.

타입 자체가 학습 의미를 갖는 legacy record는 숫자 outcome 대신 다음 native 계약을
검증한다.

```text
negative_control_case
  = 명시적 rejection/negative-control reason 또는 usable outcome

counterexample
  = 명시적 negative-control reason + screening_decision

newsless_or_unexplained_case
  = no_catalyst_asserted=true + usable outcome
```

작업 분류인 `CANDIDATE_GENERATION_MISS`, `RANKING_MISS` 등은 가격 반응 label로
해석하지 않는다. 반대로 negative-control/counterexample에 고가 +10% 이상의 강한
양의 outcome이 붙으면 타입과 수치의 모순으로 보고 AUDIT로 내린다.

### 2.2 Retrieval과 sweep

local vector index를 v3으로 올리고 record마다 다음 필드를 저장한다.

```text
evidence_polarity
label_quality
routing_disposition
polarity_classifier_version
threshold_source
threshold_role
memory_lanes
```

lane 검색은 기본적으로 `REASONING` record만 반환한다. 구조 감사가 필요하면 disposition을 명시해 AUDIT/QUARANTINED record를 별도로 조회할 수 있다. Sweep는 모든 record ID를 coverage에 남기지만 reasoning lane 본문에는 REASONING record만 넣는다.

### 2.3 Brain compile

- generic positive claim은 `record_is_positive_support()`를 통과한 record만 생성한다.
- 부정 control, near miss, newsless/unexplained는 각 polarity와 count를 유지한다.
- ineligible positive를 near miss로 재분류하지 않는다.
- compiler prompt에 네 축 계약과 AUDIT/QUARANTINED 사용 금지를 명시한다.
- prompt와 category manifest에 `positive_support_eligible`을 명시해 candidate error를 긍정 analog로 쓰지 못하게 한다.
- `llm-full` brain version은 full-envelope record hash, routing root, classifier version을 함께 봉인한다.
- accepted brain-record episode만 존재하는 Gold bundle도 first-class coverage source로 감사한다.
- record coverage manifest를 v2로 올리고 polarity·quality·disposition·4축 교차표를 저장한다.
- v1 coverage는 기존 evidence-phase audit 의미로 계속 읽고, v2부터 새 disposition 의미를 감사한다.

### 2.4 Training export

- export selection과 runtime routing이 같은 공통 라우터를 사용한다.
- record 학습 행에 `RecordRoutingMetadata v2`를 포함한다.
- manifest에 source/exported 4축 교차표를 각각 기록한다.
- exported row는 `training_eligible=true`와 `REASONING`을 모두 만족해야 한다.
- positive row는 추가로 `label_quality=verified`여야 한다.
- negative control과 newsless/unexplained는 eligible+REASONING이면 전용 학습 task로 유지한다.
- 제외 record는 원 polarity를 바꾸지 않고 AUDIT_ONLY artifact에 라우팅 근거와 함께 남긴다.
- audit는 export의 routing metadata를 현재 record store에서 독립 재계산해 exact equality로 비교한다.
- source hash는 payload만이 아니라 full record envelope를 결속한다.
- counterexample도 eligible+verified+REASONING이면 전용 calibration task로 보존한다.

## 3. 현재 968 record 실측

### Polarity

| Polarity | Count |
|---|---:|
| POSITIVE | 86 |
| NEGATIVE | 383 |
| NEAR_MISS | 27 |
| UNEXPLAINED | 214 |
| CONTEXT | 136 |
| UNKNOWN | 122 |

### Label quality

| Quality | Count |
|---|---:|
| verified | 524 |
| conflicting | 2 |
| missing | 191 |
| not_applicable | 251 |

### Routing disposition

| Disposition | Count |
|---|---:|
| REASONING | 525 |
| CONTEXT | 114 |
| AUDIT | 275 |
| QUARANTINED | 54 |

### 주요 교차 결과

```text
POSITIVE + eligible + verified + REASONING      78
  └ strict positive support                     58
  └ candidate error correction only             20
POSITIVE + ineligible/missing quality + AUDIT     8
NEGATIVE + eligible + verified + REASONING     379
NEGATIVE + eligible + conflicting + AUDIT        2
UNEXPLAINED + eligible + verified + REASONING   40
UNEXPLAINED + ineligible + verified + AUDIT    174
```

따라서 부적격·품질 미완결 긍정 8건은 POSITIVE라는 역사적 사실을 유지하지만 긍정
reasoning·학습에는 들어가지 않는다. 적격 POSITIVE 78건 중 과거 승자를 놓친
candidate error 20건은 가격 방향은 보존하되 positive analog가 아니라 오류교정 전용
lane으로만 사용한다. 반대로 학습 가능한 부정 379건과
newsless/unexplained 40건은 전용 통로에 남는다. 부정 379건 중 negative-control
375건과 counterexample 4건은 서로 다른 lane으로 유지된다.

현재 reasoning lane 분포:

```text
positive_analogs                 58
negative_controls              375
near_misses                     27
newsless_or_unexplained         40
candidate_generation_errors     20
counterexamples                  4
leader_selection_pairs           1
```

한 record가 여러 lane에 포함될 수 있으므로 lane 합계는 REASONING record 수와 같을 필요가 없다.

## 4. 검증 범위

추가·갱신한 회귀 범위:

- eligible/ineligible positive polarity 불변
- eligible negative control과 ineligible negative 분리
- near miss와 newsless strong outcome 보존
- context, audit, unknown typed 분리
- numeric outcome 누락
- explicit label과 numeric outcome 충돌
- 중첩 label quality 충돌 및 미등록 quality 값
- legacy verified outcome 계약 승계
- lane 검색의 REASONING 기본 제한
- training export 4축 교차표와 source record exact parity
- routing metadata 변조 탐지
- record coverage v2 교차표 변조 탐지
- vector index v3 schema 전환
- full-envelope hash 기반 vector/training/sweep cache 무효화
- sweep cache와 artifact lane을 현재 record에서 독립 재계산해 exact equality로 대조
- index row routing과 source record 독립 재계산 대조
- LLM-full supporting records와 audit context 물리적 분리
- 실제 import → llm-full rebuild → deep brain audit 생산 경로 회귀
- coverage v1 read compatibility

내부 gate:

```text
ruff: PASS
mypy: PASS (92 source files)
Phase 3 집중 회귀: PASS
full pytest: PASS (1,337 tests)
independent audit: APPROVE (P0/P1 0건)
```

## 5. 다음 단계와 경계

Phase 3은 record의 사용 의미를 정규화했지만 ANN, FTS, memory cell, 모집단 통계를 구현하지 않는다. 다음 Phase 4에서는 이 라우팅 metadata를 production index filter와 as-of memory cell membership의 기본 축으로 사용한다.
