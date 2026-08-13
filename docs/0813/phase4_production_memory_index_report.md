# Phase 4 production retrieval index와 as-of memory cell 보고서

상태: 구현·검증 완료, 독립감사 `APPROVE`

## 1. 목적

Phase 4는 온라인 검색에서 Python으로 전체 record를 점수 계산하는 경로를 production에서
제거한다. ANN 결과를 통계 분모로 오해하지 않고 다음 두 단계를 분리한다.

```text
HNSW ANN + FTS
  -> 관련 memory cell 후보 선택

선택된 cell의 cutoff-safe 전체 member
  -> Phase 5 관측 모집단 후보
```

기존 `memory/vector_index`는 deterministic local/test 호환 인덱스로 유지한다. production
명령과 `llm-full` rebuild는 `memory/retrieval_index/snapshots/` 아래의 불변 DuckDB
snapshot을 만든다.

## 2. 구현 계약

### 2.1 Memory cell

각 indexed record는 다음 값을 정확히 한 행으로 갖는다.

```text
primary_cell_id          정확히 1개
secondary_cell_ids       0..2개
independent_unit_id      issuer-day/theme-day/event-day/record fallback
membership_score        primary centroid cosine score
membership_rule         semantic_sign_primary_adjacent_secondary
membership_rule_version v1
available_from           cutoff 검증 기준
routing_disposition      REASONING/CONTEXT/AUDIT/QUARANTINED
```

Primary cell은 embedding 전체 차원을 사용하는 버전 고정 random-hyperplane LSH 10bit
signature로 배정한다. source code에는 종목, ticker, theme, region, beneficiary mapping을
넣지 않는다. Secondary cell은 Hamming distance 1~2인 실제 존재 cell 중 projection
경계가 가까운 최대 2개다.

Primary membership은 통계 중복 방지용이고 secondary membership은 retrieval recall 확장용이다.
Phase 5 통계는 `independent_unit_id`로 다시 dedup한다.

### 2.2 불변 as-of snapshot

Snapshot identity는 다음을 결속한다.

```text
canonical full-envelope corpus root
record-store generation root
explicit cutoff identity 또는 live partition identity
max_available_from
embedding provider/model/dimensions
clustering/normalizer/cell schema version
polarity classifier version
cell/member sidecar hash
routing metadata root
```

산출물:

```text
memory/retrieval_index/current.json
memory/retrieval_index/snapshots/<snapshot_id>/manifest.json
memory/retrieval_index/snapshots/<snapshot_id>/memory.duckdb
memory/retrieval_index/snapshots/<snapshot_id>/cells.jsonl
memory/retrieval_index/snapshots/<snapshot_id>/memberships.jsonl
memory/retrieval_index/snapshots/<snapshot_id>/source_record_hashes.jsonl
```

같은 snapshot ID의 다른 bytes는 덮어쓰지 않는다. 과거 cutoff 조회는 hash-bound registry에서
같은 embedding model과 cutoff 계약을 만족하는 최신 snapshot을 선택한다. 같은 cutoff에
backfill이 추가되면 기존 snapshot은 stale이 된다.
따라서 현재 corpus로 만든 cell membership을 과거 replay에 역주입하지 않는다.

### 2.3 증분 import

동일 embedding model의 직전 snapshot과 full-envelope hash가 같은 record는 DuckDB에 저장된
embedding을 재사용한다. 변경·추가된 record만 embedding provider에 전송한다.

회귀 fixture 결과:

```text
첫 snapshot 1 record  -> embedding 1건
두 번째 snapshot +1  -> 기존 1건 재사용, 신규 1건만 embedding
retained_record_count = 1
added_record_count    = 1
```

Embedding model이 바뀌면 snapshot ID와 호환 집합이 달라지며 이전 vector를 재사용하지 않는다.

## 3. DuckDB index stack

```text
metadata indexes
  available_from, routing_disposition, record_type, primary_cell_id

FTS/BM25
  reasoning_records.document

HNSW cosine ANN
  cells.centroid

provenance graph
  provenance_edges(record_id, source_id)
```

Production build는 factory 검증 뒤 발급된 attested async provider adapter가 아니면 시작
단계에서 실패한다. 이 attestation은 운영 구성 검증이지 암호학적 provider 증명은 아니다.
FTS 또는 VSS extension,
HNSW index, provenance/metadata index 중 하나라도 준비되지 않으면 `production_ready=false`다.
DuckDB VSS의 disk persistence는 현재 experimental 설정이므로 snapshot을 immutable하게 만들고
DB/sidecar/source를 audit에서 교차검증한다. Online query는 작은 record-store generation
manifest와 registry를 확인하고 DB 파일 SHA를 `(snapshot_id, size, mtime)`별 최초 1회 검증한다.

공식 구현 근거:

- DuckDB VSS/HNSW: https://duckdb.org/docs/lts/core_extensions/vss
- DuckDB FTS/BM25: https://duckdb.org/docs/lts/core_extensions/full_text_search

## 4. 독립 검증 경로

`inspect_memory_snapshot()`은 다음을 source store와 DB에서 독립 재계산한다.

```text
artifact hash
source full-envelope record hash
record ID set
primary membership 1:1
secondary membership exact set
cell sidecar counts
routing disposition counts
provenance edge set
metadata/HNSW/FTS index 존재
future member count = 0
```

`audit coverage`, `doctor`, `production readiness`, `memory inspect-index`가 이 inspection을
노출한다. Online `search_cells`와 `members_for_cells`는 source JSONL을 다시 읽지 않고
snapshot manifest와 DuckDB만 사용한다.

## 5. 현재 store 실측

현재 store의 968 record는 live cutoff-safe 966건과 future 2건으로 분리된다. 별도 임시
root의 production-shaped attested adapter fixture는 실제 외부 API가 아니라 deterministic
vector를 반환하므로 구조·transaction smoke test로만 사용했다.

```text
record_count                  966
excluded_future_record_count  2
primary_membership_count      966
cell_count                    276
secondary_membership_count    1,932
future member                 0
```

600건 production-code streaming build/inspection과 threshold 강제 streaming-audit branch
tamper 회귀를 통과했다. 10,001건 이상 재현 artifact는 아직 저장하지 않았다. 1536차원 실제
외부 provider의 60만 건 build, peak RSS와 API 시간은 아직 실측하지 않았다.

## 6. 50k/200k/600k SQL query microbenchmark

재현 명령:

```bash
python -m news_scalping_lab.tools.profile_memory_index --query-repeats 7
```

환경:

```text
DuckDB 1.5.4
32-dimensional vectors
1,024 cells
synthetic SQL rows
```

| Records | Build | DB bytes | HNSW median | FTS median | Cell-member median |
|---:|---:|---:|---:|---:|---:|
| 50,000 | 0.877s | 5,255,168 | 1.149ms | 108.544ms | 1.495ms |
| 200,000 | 1.732s | 15,478,784 | 1.705ms | 259.950ms | 2.720ms |
| 600,000 | 4.697s | 41,431,040 | 1.415ms | 414.284ms | 3.199ms |

세 크기 모두 `EXPLAIN`에서 `HNSW_INDEX_SCAN`을 확인했고 cutoff 이후 member는 0이었다.
FTS 부하는 모든 synthetic document가 검색어와 일치하는 최악형 fixture다.

이 표는 32차원·축소 schema의 DuckDB query microbenchmark이며 Phase 4의 60만 production
builder 종료 증거가 아니다. 60만 real payload, 1536차원 embedding, production sidecar와
secondary/provenance 분포, peak RSS, 한국어 검색 품질과 recall은 Phase 8/9에서 측정한다.

## 7. CLI

```bash
# local/test deterministic JSONL index
nslab memory rebuild-index

# real embedding + immutable FTS/HNSW/cell snapshot
nslab memory rebuild-index --production
nslab memory rebuild-index --production --as-of 2026-08-14T08:59:59+09:00

nslab memory inspect-index
nslab memory search-cells "공급계약 수혜 경로" \
  --cutoff-at 2026-08-14T08:59:59+09:00 \
  --include-members
```

## 8. Phase 4 종료 판단

```text
online Python full corpus score scan               제거
50k/200k/600k 축소 SQL HNSW query plan             확인(성능 종료 gate 아님)
600 production-code streaming build/audit         확인
10,001+ bounded audit 재현 artifact                미생성
600k 1536D production-shape peak RSS/API profile  미실측, Phase 8/9 blocker
모든 indexed record primary membership             확인
CONTEXT/AUDIT/QUARANTINED disposition 보존          확인
과거 cutoff에서 future membership                  차단
동일 model 증분 embedding 재사용                   확인
model 변경 시 snapshot/cache invalidation           확인
Phase 4 독립감사                                   APPROVE, P0/P1 0건
```

Phase 5는 ANN 후보 자체가 아니라 선택 cell의 cutoff-safe 전체 member를 읽어 관측 모집단과
통계 cube를 만든다.
