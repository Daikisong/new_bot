# Phase 5 모집단 통계 구현 보고서

상태: APPROVE (bounded implementation), production 승격 차단

## 1. 목적

Phase 5는 ANN 상위 record를 통계 분모로 쓰지 않는다. ANN과 FTS는 관련 memory cell을
고르는 데만 사용하고, 선택된 cell의 cutoff-safe 전체 구성원을 SQL에서 읽어 관측 모집단을
만든다.

```text
query
→ relevant cell IDs
→ selected cells의 primary/secondary 전체 member
→ independent unit type 필터
→ unit 중복 제거
→ deterministic statistics/cube
```

## 2. 구현 범위

주요 모듈:

```text
src/news_scalping_lab/memory/population.py
src/news_scalping_lab/memory/statistics.py
src/news_scalping_lab/memory/index.py
src/news_scalping_lab/contracts/memory_context.py
```

운영 명령:

```text
python -m news_scalping_lab.cli memory build-population ...
python -m news_scalping_lab.cli memory inspect-population <manifest>
```

`build-population`은 `--population-purpose`로 catalyst_response, candidate_error,
newsless, leader_selection을 명시한다. purpose별 canonical lane과 허용 unit type이 contract에
고정되어 있어 task 교정 record가 시장반응 분모에 섞이지 않는다.

production memory DB schema v2는 각 record에 다음 결정론적 projection을 저장한다.

```text
independent_unit_type
path_type
regime_cluster
high_return_pct
close_return_pct
upper_limit_touched
outcome_observed
sample_weight
```

outcome과 routing 필드는 cell embedding에 넣지 않는다. 이 필드는 cell 선택 이후 통계와
분포 집계에만 사용한다.

## 3. 독립 표본 계약

record 목적에 따라 다음 단위를 사용한다.

```text
direct event         event-issuer-day
issuer total         issuer-day
theme formation      theme-day
theme beneficiary    theme-day-ticker-day
leader comparison    theme-day-pair
newsless/unexplained ticker-day
```

같은 unit에 여러 record가 있으면 가격 반응 투표권은 한 표다. raw record 수와 independent
unit 수는 manifest에 별도로 기록한다. 동일 unit의 outcome 값이 충돌하면 중앙값으로 세탁하지
않고 missing으로 내린다.

## 4. 모집단과 cube

관측 모집단 조회는 `REASONING` disposition으로 고정한다. CONTEXT/AUDIT/QUARANTINED는 전수
coverage와 별도 감사 경로에 보존되지만 반응률 분모에는 넣지 않는다. polarity lane을 먼저 골라 분모를 만들지 않으므로 positive/negative
결과에 조건부인 ANN top-K 비율을 모집단 비율로 오인하지 않는다.

cube 차원:

```text
cell
x memory_lane
x time_slice
x regime_cluster
x record_type
x path_type
x label_quality
```

각 cube row는 raw record 수, unit 수, effective sample size, polarity counts, outcome summary,
member/unit ID hash를 가진다. time slice는 전체, 최근 1년, 최근 3년, 과거 3~10년,
10년 초과와 명시적 유사 regime를 지원한다.

## 5. 관측률 계약

다음 네 지표만 deterministic 관측률로 제공한다.

```text
upper_limit_touched
high_return_5
high_return_10
high_return_20
```

각 지표의 denominator는 그 지표를 실제 관측한 unit만 포함한다. outcome 누락은 실패나 음성으로
세지 않는다. raw numerator/denominator와 함께 sample-weighted numerator/denominator를 보존하며,
관측률, high/close mean·median·p10·p25·p75·p90, confidence interval은 모두 같은 unit
weight를 사용한다. 한 issuer-day의 여러 event는 합산 투표권 1을 넘지 않는다.

표현 계약은 `observed_population_rate`다. walk-forward calibration 전에는 이 수치를 예측 확률로
표현하지 않는다.

## 6. 운영 예산

현재 Python aggregation은 SQL count 이후 최대 50,000 selected records, 250,000 cube rows로
fail-closed한다. 이 제한은 표본 추출 제한이 아니다. 초과 모집단은 일부만 계산하지 않고 중단하며,
Phase 6의 cell 세분화와 adaptive drill-down 대상으로 넘긴다.

Windows 동일 환경의 synthetic unique issuer-day compute-only profile:

```text
50,000 records   1.563s   peak working set 277.055 MiB   diagnostic only
200,000 records  8.624s   peak working set 894.074 MiB   diagnostic only
600,000 records 29.311s   peak working set 2,518.141 MiB diagnostic only
```

실제 record store→memory index→population artifact build→mandatory self-inspection을 포함한 50,000
end-to-end profile은 343.525초, peak working set 1,080.289 MiB였다. 따라서 현재 Phase 5를
unbounded production-ready라 부르지 않는다. 5만 초과는 SQL count에서 artifact materialization
전에 차단하며, 5만 이하도 현재는 bounded 기능 완료 상태다. Phase 6에서 관련 cell을 더 세분화해
각 full-member population은 보존하되 실행 단위를 낮춰야 production 승격할 수 있다.

## 7. 무결성과 재현성

`PopulationManifest v2`는 다음에 결속된다. Phase 5에서 필수 projection과 독립 단위
감사 필드가 추가된 memory snapshot 계약은 `nslab.memory_cell_snapshot_manifest.v2`,
구조 projection은 `cutoff_safe_structural_projection.v4`로 올렸다. 구 v1 snapshot은
legacy 보존 대상이지만 현재 production 선택 대상은 아니다.

```text
run_id / cluster_id / cutoff
memory snapshot ID
record-store source generation
corpus manifest hash
selected cell IDs
independent unit type
routing dispositions
statistics/cube/bootstrap versions
member/unit/cube artifact hashes와 counts
```

population artifact는 immutable하게 기록한다. `inspect-population`은 memory DB에서 raw members를
다시 읽고 unit, cube, outcome, observed rates, membership hash를 전부 재계산한다. 현재 source
generation과 맞지 않는 snapshot, DB 변조, artifact 변조, ID 불일치는 fail-closed다.

## 8. 내부 검증 결과

```text
선택 cell 전체 membership 사용                       PASS
3 reasoning issuer-day records -> 2 independent units PASS
newsless의 issuer/event 모집단 자동 유입 0            PASS
missing outcome의 negative/실패 변환 0                PASS
incremental/full snapshot population artifact parity PASS
member/unit/cube 재계산 parity                         PASS
artifact tamper detection                             PASS
purpose/lane/unit contract tamper detection            PASS
50k end-to-end low-cardinality cube profile            PASS, production latency blocker
10k unique-cell / 30k cube-row compute profile          PASS, 250k budget 미검증
schema export parity                                  PASS
ruff                                                   PASS
mypy 98 modules                                        PASS
pytest 1,397 tests                                     PASS
외부 독립감사                                          APPROVE (bounded)
```

## 9. 남은 범위

Phase 5는 5만 이하 모집단 계산과 provenance를 닫는다. 5만 초과 또는 현재 50k latency는 Phase 6
세분화 전 production blocker다. 대표 사례 다양성, MMR, 불확실성 기반 추가 탐색은
Phase 6에서 구현한다. 600k 1536D 실제 provider/RSS/한국어 recall과 walk-forward calibration은
Phase 8/9 종료 조건으로 유지한다.

현재 968-record store를 strict 독립 단위 계약으로 재분류하면 REASONING 64건이
`UNSUPPORTED_RECORD`다. 구성은 direct-event 48, theme 7, beneficiary 4, counterexample 4,
leader-pair 1이다. 이들은 record ID fallback으로 통계 분모에 세탁하지 않으며 snapshot의
`unsupported_reasoning_record_count`와 ID hash에 결속한다. count가 0이 아니면
`production_ready=false`다. 원본 구조 ID를 repair하거나 명시적으로 AUDIT 격리하기 전까지
현재 corpus의 production population 승격은 차단된다.

고카디널리티 실측은 unique-cell 10,000 records에서 30,000 cube rows, 4.05초,
tracemalloc peak 129.3 MiB였다. 선언된 250,000-row 상한의 end-to-end 안전성은 아직
증명되지 않았다. 따라서 record budget과 cube budget 모두 Phase 6에서 낮추거나
streaming 집계로 대체해야 하는 운영 blocker로 유지한다.
