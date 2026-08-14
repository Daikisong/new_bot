# Phase 7 일일 메모리와 final synthesis 통합 보고서

상태: APPROVE (bounded Phase 7)

## 1. 목적

Phase 7은 Phase 4-6의 immutable memory snapshot, population, representative, adaptive
retrieval을 일일 BLIND 분석의 final synthesis에 연결한다. 온라인 경로는 전체 record 또는
compiled claim corpus를 Python으로 다시 읽지 않는다. 모든 memory-dependent 판단은 cutoff,
source generation, artifact path/hash와 selected representative provenance에 결속한다.

## 2. 실행 흐름

```text
open-world prediction + candidate verification + red team
  -> cutoff-safe BeneficiaryGraph v2
  -> production memory snapshot resolve
  -> matching immutable llm-full brain + CategoryBrainIndex resolve
  -> material cluster query embedding 1회 + claim ANN top-3
  -> purpose별 cell search
  -> population -> representative -> typed adaptive retrieval
  -> global 48 KB compact allocator
  -> DailyMemoryContext v2
  -> FinalSynthesisContext v3
```

production memory가 ready가 아니면 기존 final v2를 유지하고 Phase 7은 명시적으로 skip한다.
ready 상태에서 필요한 artifact, real embedding identity 또는 nested inspection이 하나라도
불일치하면 final LLM 호출 전에 fail-closed한다.

## 3. Category Brain Index

llm-full compiler는 cutoff-safe `CompiledBrainClaim`을 한 번 임베딩하여 immutable DuckDB HNSW
index와 vector ledger를 만든다. index identity는 brain version, exact brain cutoff, embedding
model/dimension, claim artifact/hash/count와 database/vector hashes를 포함한다.

일일 실행은 material cluster마다 query만 한 번 임베딩하고 ANN top-3 claim으로 expanded query를
만든다. 전체 claim 재임베딩, Python cosine 전수 정렬, cluster마다 DB 전체 재해시는 없다.
runtime은 cached immutable hash gate를 사용하고, explicit daily/provenance/lookahead inspection은
deep ledger/DB/source parity를 재계산한다.

선택 claim proof는 canonical claim payload hash와 Merkle inclusion proof를 함께 기록한다. Merkle
root는 immutable category index manifest에 결속된다. local inspector는 DuckDB 원문과 proof
payload를 exact 비교하고, standalone importer는 embedded proof를 root에 대해 독립 검증한다.
따라서 claim ID를 유지한 채 statement/mechanism과 상위 artifact hash를 함께 바꾸는 coherent
rewrite도 거부한다.

query plan은 다음을 기록한다.

```text
original/expanded query와 hash
embedding model, query vector hash
selected claim IDs, vector hashes, scores
immutable category index manifest path/hash
usage = QUERY_PLANNER_NOT_EVIDENCE
```

standalone importer는 embedded event cluster manifest/rows에서 original query를 다시 만들고, Merkle
검증된 selected claims로 `expanded_category_query`를 재실행한다. 따라서 query plan, compact와 final
hash를 함께 바꿔도 임의 prompt 문자열을 주입할 수 없다. ANN top-K score의 독립 재실행은 provider와
index DB를 가진 local deep inspector의 책임이며 standalone은 이 한계를 명시적으로 유지한다.
category guidance도 verified selected claims, representative selected record IDs, cutoff와 proof artifact
reference로 pure 재투영하여 statement/mechanism prompt injection을 같은 경계에서 거부한다.

final bundle에는 전체 compiled claims나 vector ledger를 복제하지 않는다. query plan 및 guidance에
실제로 사용된 selected claims만 별도 JSONL proof로 포함한다. compiler가 category index 생성 뒤
실패해 orphan이 남아도 exact claims/model/version inspection 후 동일 build를 재시도할 수 있다.

## 4. Purpose별 모집단

각 material cluster는 다음 purpose를 독립적으로 시도한다.

```text
catalyst_response:
  event-issuer-day, issuer-day, theme-day, theme-day-ticker-day
candidate_error:
  event-issuer-day, issuer-day, theme-day-ticker-day
newsless:
  ticker-day
leader_selection:
  Phase 8의 cutoff-safe current pair evidence가 생길 때까지 명시적 deferred
```

`built_population_keys`는 `(cluster, purpose, unit)`의 exact unique set이다.
`uncovered_population_purposes`는 모든 material cluster에 대해 시도한 purpose의 여집합을 기록한다.
inspector는 population/representative/adaptive Counter가 각 key별 정확히 1인지, final adaptive
reference와 purpose/unit identity가 같은지 독립 재계산한다. candidate-error와 newsless가 catalyst
반응률 분모로 섞이지 않는다.

## 5. Beneficiary Graph v2

graph는 immutable candidate input JSONL, material event manifest, cutoff-safe candidate-matched company
memory 파일에서 exact 재생성한다. 회사 메모리는 `available_from`과 `known_at`이 모두 cutoff
이하이어야 하며 ticker/company/aliases가 같은 여러 delta 파일의 business roles를 deterministic
union한다.

standalone importer도 embedded candidate input, event cluster manifest/rows와 company-memory 원문을
같은 pure projector에 넣어 graph 전체를 exact 재생성한다. local/standalone 검증이 동일한 derivation
contract를 사용하므로 bundle에서 business role, mechanism 또는 source 관계만 다시 쓰는 공격도
거부한다.

`mechanism_steps`에는 실제 candidate causal chain만 들어간다. thesis/why-now는 narrative field로
분리되므로 causal hop이 없는 후보가 MULTI_HOP으로 오분류되지 않는다. typed trigger는 material
cluster, 실제 causal step 2개 이상, source IDs, graph artifact/hash와 derivation version을 모두
요구한다. prompt 실행 시각의 synthetic provenance는 evidence source로 사용하지 않는다.

graph는 후보 whitelist가 아니다. 그러나 Final v3가 seal할 후보 identity는 pre-final candidate,
candidate verification의 `final_candidate` subject, graph path에 모두 존재해야 한다.

## 6. Bounded Daily Context

`DailyMemoryContext v2`는 원본을 embedding하지 않고 immutable references를 보존한다.

```text
news/event/memory coverage
memory snapshot/corpus/source generation
purpose별 population manifests
representative manifests
adaptive v4 traces
category brain/index manifest와 selected-claim proof
beneficiary graph
compact final context
supporting/contradicting/unexplained selected record IDs
```

compact context는 full manifests, vector metadata와 company hash maps를 제외한다. 모든 material
cluster의 purpose coverage, slim population/query 정보를 먼저 넣고, representative와 graph path를
cluster round-robin으로 추가한다. omitted counts를 기록하며 대표가 존재하는 cluster는 최소 한 건을
보존하지 못하면 fail-closed한다. exact canonical JSON 기준 48,000 bytes hard cap이다.

standalone importer는 embedded population manifests, representative manifests/JSONL, query plans,
guidance와 beneficiary graph에서 동일 pure compact projector를 실행한다. compact와 상위 hash를 함께
바꾸더라도 population counts, role IDs, disagreements 또는 selected excerpts가 원료와 다르면 거부한다.

실제 production-shaped 1-cluster chain과 이를 11개 material cluster로 확장한 회귀에서 모든 cluster
대표를 보존하고 48 KB 이하를 유지한다. 이는 정상 11-cluster day가 구조적으로 실패하던 이전 full
embedding payload를 대체한다.

## 7. Final v3와 provenance

Final v3 payload는 daily/graph 원본 객체를 다시 넣지 않는다. 다음 bounded projection만 전달한다.

```text
daily/graph immutable path + hash
memory snapshot, purpose coverage, selected role IDs
compact context
graph path count, candidate input hash, unresolved IDs
```

legacy top-K episode/record IDs는 v3 기본 provenance로 재주입하지 않는다. 기본 positive/negative
record IDs는 DailyMemoryContext의 supporting/contradicting selected IDs만 사용한다. LLM이 반환한
candidate/sector memory IDs도 역할별 selected set의 부분집합이어야 하며 episode IDs는 허용하지
않는다.

final LLM은 pre-final 후보의 `(rank, ticker, company, path)` identity를 추가, 제거, 교체 또는 재정렬할
수 없다. 신규 후보가 필요하면 별도 cutoff-safe verification/graph pass가 선행되어야 한다.

## 8. 감사와 standalone bundle

daily inspector는 모든 population, representative, adaptive trace, category query plan, selected claim,
beneficiary graph와 compact bytes를 재계산한다. strict Phase7 inspection에는 production memory index가
필수다. lookahead/provenance/reporting CLI는 Phase7 artifact가 있을 때만 real provider/index를 lazy
초기화하며 legacy/mock 프로젝트는 provider 없이 기존 감사를 계속 실행한다.

local과 standalone은 같은 artifact-chain validator로 population/representative/adaptive의 exact
`(cluster, purpose, unit)` Counter, run/cutoff/snapshot/source/corpus identity, representative JSONL의
selected record/unit IDs, final trace refs, graph trigger evidence와 built/uncovered purpose를 검증한다.

standalone importer는 reachable artifact 역할별 exact schema allowlist를 사용한다. unknown/older schema,
absolute path, `..`, whitespace/Windows path alias를 거부한다. 전체 claim/vector corpus 대신 bounded
selected-claim proof를 검증하며 source hash, embedded hash, line ending, item count와 reference closure를
확인한다.

self-contained hash만으로 coherent rewrite의 출처를 인증할 수 없으므로 Phase 7 transport 전체 artifact
metadata를 `HMAC-SHA256`으로 서명한다. exporter는 local deep inspection 뒤
`NSLAB_PHASE7_TRANSPORT_HMAC_KEY`로 run/date/cutoff와 exact embedded closure를 서명하고, importer는
동일 운영 key가 없거나 signature/key ID/commitment가 다르면 fail-closed한다. key는 최소 32 UTF-8
bytes이며 bundle에 저장하지 않는다. 이는 transport authenticity용 symmetric operational attestation이고
제3자 non-repudiation 서명은 아니다.
export와 CLI single/batch import, provenance audit는 모두 `Settings.env_value`로 process environment와
project `.env`를 같은 우선순위 규칙으로 해석한 key를 전달한다. 공개 `research import-bundle`과
version-aware importer도 Phase 7 표식이 있는 v1 bundle은 저장 전에 같은 strict parser/HMAC preflight를
통과해야 하므로 command 선택으로 인증을 우회할 수 없다.

source-chain projector는 news/event/memory coverage run·cutoff·completeness와 llm-full production brain의
snapshot/corpus/source-generation/cutoff/category-index identity를 local/standalone에서 동일 검증한다.
news는 covered=input 및 missing=0, event는 news와 같은 input count 및 unassigned/duplicate=0이어야 한다.
representative는 linked population의 cluster, raw record count와 independent unit count까지 exact 일치해야
한다. guidance는 producer가 선택한 claim ID 집합만 재투영하므로 24개 limit 이후 category ordering이
달라도 valid bundle을 거부하지 않는다.

## 9. 운영 제한

Phase 7은 bounded implementation이다.

```text
current imported corpus unsupported REASONING 64 -> production memory readiness false
50k population E2E/high-cardinality cube -> Phase 5/6 production blocker 유지
real 1536D provider 600k peak RSS/latency -> Phase 8/9 실측 필요
leader-pair typed trigger -> cutoff-safe current pair artifact 전까지 deferred
```

따라서 현재 corpus의 일반 daily run은 production memory가 repair되어 ready가 되기 전까지 final v2로
남는다. 이 fallback을 Phase 7 production success로 해석하지 않는다.

## 10. 검증

현재 고정된 회귀 범위:

```text
future category claim build rejection
category index crash/orphan deterministic retry
company memory available_from/known_at/file hash and multi-delta union
historical candidate provenance와 causal-hop trigger
purpose별 population exact chain/multiplicity
11 material-cluster compact capacity
semantic query plan/vector/score deep recomputation
selected category claim payload/Merkle inclusion proof tamper rejection
standalone beneficiary graph embedded-source exact reprojection
standalone compact embedded-source exact reprojection
standalone population/representative/adaptive chain multiplicity rejection
standalone category original/expanded query prompt reprojection
standalone category guidance prompt reprojection
authenticated Phase 7 transport closure and wrong-key rejection
coverage/brain snapshot and representative/population identity rejection
final candidate identity와 selected-memory provenance
unknown schema/path traversal standalone rejection
lookahead strict index와 legacy provenance lazy provider
```

로컬 고정 트리 검증 결과:

```text
ruff check . PASS
mypy 106 source modules PASS
pytest 1,431 PASS (193.2s)
schema model/export parity PASS
git diff --check PASS (기존 line-ending 안내만 존재)
```

외부 독립 감사 결과는 `APPROVE`이며 P0/P1 잔여 finding은 없다. 감사자는 공개
`research import-bundle`의 missing transport closure가 종료 코드 1과 zero-write로 거부되고 legacy v1은
계속 지원되는지 직접 재현했다. 독립 gate는 targeted 16 PASS, full pytest 1,431 PASS (176.9s), ruff,
mypy 106 modules, schema parity와 diff-check PASS다.
