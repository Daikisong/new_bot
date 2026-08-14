# Phase 6 대표 사례와 adaptive drill-down 구현 보고서

상태: bounded implementation 완료, 외부 독립감사 APPROVE

## 1. 목적

Phase 6은 Phase 5의 전수 모집단 통계를 바꾸지 않는다. LLM에 전달할 raw record만
제한된 token budget 안에서 선택한다. 대표 사례는 모집단 분모가 아니며, 선택 결과는
원 population unit, source provenance, query embedding으로 다시 계산할 수 있어야 한다.

## 2. 대표 선택 계약

`RepresentativeSetManifest v3`와 `RepresentativeRecord v1`은 다음을 결속한다.

```text
run / cluster / cutoff / query / query embedding hash
population manifest / memory snapshot / source generation / corpus hash
selection version stratified_mmr_facility.v2 / embedding model
candidate record pool 최대 512
entropy 기반 초기 목표 8~16, 필요 시 최대 32
token upper bound 24,000
trade-date concentration 8 / issuer-theme key concentration 4
stratum coverage / population share 최대 오차 0.25
representative JSONL hash와 count
```

한 independent unit에 여러 record가 있으면 record별 lane, role, path, family,
record quality를 후보로 보존한다. 동일 strata 중복 record는 축약하고, 후보 pool은 먼저
unit reservoir를 확보한다. 최종 결과는 unit당 정확히 한 record만 허용하므로 한 unit의
record 수가 투표권을 늘리지 않는다.

필수 소수 strata는 polarity, role, record quality, path class, record family, memory lane이다.
outcome, age, regime class, unit quality는 분포 보존과 optional coverage에 사용한다. 선택은
query relevance, embedding diversity, 새 strata coverage, bounded facility-location gain,
population distribution gain을 함께 계산한다. 초기 목표를 채웠더라도 최대 share error가
0.25보다 크면 최대 32건까지 부족한 strata를 보충한다.

`context_excerpt`는 production memory DB의 cutoff-safe structural document만 사용한다.
D-day outcome 본문은 대표 문맥에 넣지 않고 selection metadata에만 남긴다. 최종 JSON
직렬화 전체의 보수적 token upper bound를 record마다 고정점으로 계산하고, manifest 한도는
JSONL 줄바꿈까지 포함한 실제 artifact byte 수에 건다.

## 3. Adaptive 계약

`AdaptiveRetrievalTrace v3`는 현재 artifact에서 독립적으로 검증 가능한 trigger만 쓴다.

```text
small effective sample size
polarity conflict
regime disagreement
low optional representative coverage
high unexplained share
```

`multi-hop beneficiary`와 `leader pair disagreement`는 bare hint로 받지 않는다. 이 두 신호는
current-event graph/pair evidence, cutoff, source IDs, artifact hash가 필요한 Phase 7 typed
provenance 계약으로 이관했다.

```text
max depth       2
max cells      12
max records    32
max tokens 72,000 cumulative
min information gain 0.03
cells per iteration 2
```

polarity, unexplained, regime trigger는 canonical lane/regime expansion plan을 만든다. production
memory snapshot v3는 lane, regime, lane-regime별 facet-qualified cell table과 HNSW index를
build 시점에 생성한다. 필터 검색은 facet HNSW와 독립 FTS union을 사용하며 record embedding 전체를
온라인 cosine scan하지 않는다.

각 iteration은 expansion query/vector hash, lane/regime filters, expanded population과 새
representative manifest를 immutable ref로 남긴다. information gain은 ESS 증가, optional
coverage 증가, polarity/regime entropy와 missing-outcome 감소로 계산한다. trigger가 없거나
추가 cell이 없거나 budget/gain 한도에 도달하면 명시적 `stopped_reason`으로 종료한다.

## 4. 무결성과 버전

```text
production memory index identity       v3
memory cell snapshot manifest          v3
memory cell schema                     memory_cell_snapshot.v3
representative set manifest            v3
adaptive retrieval trace               v3
```

이전 v1/v2 snapshot/representative/adaptive artifact는 legacy 또는 stale로 읽되 current
production 입력으로 선택하지 않는다. `inspect-representatives`는 population, DB, source rows,
selection scores, distribution error, token counts와 artifact bytes를 전부 재계산한다.
`inspect-adaptive`는 initial/final/iteration hash를 검증한 뒤 trace를 다시 실행해 exact
equality를 요구한다. reasoning cell facet projection도 source DB에서 독립 재계산한다.

## 5. 측정

Windows local compute-only synthetic 50,000-unit profile:

```text
input units             50,000
candidate pool             512
selected records            16
elapsed                  5.781s
tracemalloc peak       100.556 MiB
selection sha256       2dbbae4a11caf06740a124336b207d7b6c883e2dc79147708a11b11b9f151622
```

이 수치는 synthetic member/unit row 생성, strata pool, 32D MMR/facility/distribution selection을
포함하는 compute-only 진단이다. DuckDB read, real 1536D query embedding, population build,
JSONL I/O, mandatory inspection을 포함한 production E2E 수치가 아니다. Phase 5에서 기록한
50k population latency, 250k cube budget, current corpus unsupported REASONING 64건이 먼저
해결되어야 production Phase 6을 측정하고 승격할 수 있다.

## 6. 검증 범위

```text
one-record-per-independent-unit                         PASS
record-rich unit candidate-pool domination 방지         PASS
member-specific minority lane/role/quality 보존          PASS
facility-location과 population distribution 오차        PASS
date / issuer-theme concentration bounds                 PASS
final serialized token upper bound                       PASS
query/model/population/source identity binding           PASS
representative artifact tamper detection                 PASS
lane/regime facet search의 record-vector full scan 0     PASS
adaptive max-depth/cell/record/token bounds               PASS
adaptive trace exact recomputation/tamper detection       PASS
CLI build/inspect commands                                PASS
```

## 7. 운영 제한

이 구현은 bounded Phase 6이다. Phase 6 산출물은 아직 daily analyzer/final synthesis에 주입하지
않는다. 그것은 Phase 7 category brain, beneficiary graph, final payload 통합 범위다. current
corpus structural-ID repair, broad population E2E latency, high-cardinality cube, real 1536D
provider E2E profile이 해결되기 전에는 production-ready로 주장하지 않는다.

## 8. 최종 gate

```text
ruff PASS
mypy 101 modules PASS
pytest 1,413 PASS
schema model parity mismatch 0
git diff --check PASS
외부 독립감사 APPROVE (bounded Phase 6)
```
