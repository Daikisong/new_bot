# Phase 1 뉴스 전수 커버리지와 의미 사건 군집화 보고서

상태: 완료, 독립 감사 APPROVE

## 1. 목적

기존 일일 분석은 provider 종류와 무관하게 뉴스 앞 12개만 Pass 0에 전달할 수 있었다.
Phase 1은 이 제한을 제거하고 다음을 보장한다.

```text
CSV의 모든 행을 strict parse한다.
각 행은 정확히 하나의 cluster 또는 duplicate parent에 배정한다.
cutoff 밖 행은 보존하되 BLIND material 분석에서 제외한다.
모든 material cluster는 open-world Pass 0에서 정확히 한 번 분석한다.
LLM이 분석했다고 자기 선언하는 것만으로 통과시키지 않는다.
원본 CSV부터 최종 artifact까지 ID와 hash를 다시 계산해 감사한다.
```

## 2. 구현 흐름

```text
전체 CSV
→ cutoff/window 분류
→ full-body exact duplicate 분리
→ bounded embedding batch
→ complete-link semantic merge
→ issuer/predicate/state/counterparty/numeric 구조 gate
→ bounded material cluster
→ bounded Pass 0 prompt batch
→ cluster_id별 OpenWorldClusterFinding
→ strict coverage·cluster·Pass 0 artifact
→ CLI/provenance/diagnostics 독립 교차검증
```

핵심 구현은 다음 파일에 있다.

```text
src/news_scalping_lab/inference/event_clustering.py
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/contracts/memory_context.py
src/news_scalping_lab/contracts/models.py
src/news_scalping_lab/audits/provenance.py
src/news_scalping_lab/cli.py
src/news_scalping_lab/diagnostics.py
```

## 3. 군집화 계약

### exact duplicate

제목과 본문 전체를 공백·대소문자 정규화한 뒤 hash한다. embedding용 본문은 길이를
제한할 수 있지만 exact fingerprint는 본문 뒷부분까지 전부 사용한다. 따라서 앞 4KB가 같고
끝의 거래상대나 조건이 다른 기사는 exact duplicate로 접히지 않는다.

### semantic duplicate

embedding 유사도만으로 합치지 않는다. 두 기사가 모두 다음 구조 gate를 통과해야 한다.

```text
issuer anchor 일치
predicate/action state 비충돌
거래상대 비충돌
KRW/USD/PCT quantity 비충돌
complete-link 최소 유사도 threshold 충족
cluster당 semantic variant 상한 충족
```

구조가 불확실하면 합치지 않는 보수적 정책이다. 종목·ticker·theme·region·beneficiary
목록은 사용하지 않는다.

숫자는 `1조원 = 10000억원`처럼 통화 단위를 canonical quantity로 바꾼다. 반대로 공통
연도 2026이 있더라도 `100억원`과 `200억원`은 다른 사건으로 유지한다.

### 반대 사건과 거래상대

다음 adversarial pair는 embedding이 완전히 같아도 분리한다.

```text
계약 체결 / 계약 해지
유상증자 결정 / 유상증자 철회
사업 추진 / 사업 무산
허가 신청 / 허가 반려
A사에 공급 / B사에 공급
A사 대상 / B사 대상
```

## 4. 전수 커버리지 계약

새 strict artifact는 다음과 같다.

```text
NewsCoverageManifest
EventClusterManifest
OpenWorldFirstAnalysis v2
```

각 뉴스 행은 다음 identity를 유지한다.

```text
row_number
event_id
source_id
primary_cluster_id 또는 duplicate_parent_cluster_id
disposition
```

`EventClusterManifest`는 같은 순서의 row/event/source ID를 가진다. CLI와 provenance audit는
서로 다른 artifact끼리만 비교하지 않고 SHA-bound 원본 CSV를 다시 파싱해 event/source ID와
cutoff disposition을 재계산한다. cutoff 밖 행은 반드시 AUDIT_ONLY이고 material Pass 0에
들어가지 않는다.

모든 cluster artifact는 대표 기사뿐 아니라 각 member의 row/event ID, 제목·본문 hash와
excerpt를 보존한다. 따라서 representative 선택 때문에 다른 기사에만 있던 사실이 조용히
사라지는 것을 감사할 수 있다.

## 5. Open-world Pass 0 계약

`OpenWorldFirstAnalysis v2`는 단순한 `analyzed_cluster_ids` 자기 선언 외에 material
cluster마다 정확히 한 개의 `OpenWorldClusterFinding`을 요구한다.

```text
cluster_id
event_summary
mechanisms 또는 uncertainties
direct_candidates
potential_sectors
```

dispatched cluster ID 순서와 finding ID 순서가 정확히 같아야 한다. 12개 ID를 적고 generic
요약 한 줄만 내는 응답은 즉시 실패한다. real LLM이 빈 semantic 필드를 반환해도 heuristic
값으로 세탁하지 않는다. 정상 structured response가 불완전하면 `OpenWorldCoverageError`로
중단한다. provider가 명시적으로 `NotImplementedError`를 내는 test fallback 경로만 결정론적
finding을 만든다.

Pass 0은 cluster 개수와 실제 JSON prompt 문자 수를 함께 제한한다. 한 cluster 자체가 hard
budget을 넘으면 truncate하지 않고 명시적으로 실패한다. 이는 뉴스 누락보다 운영 오류를
드러내는 쪽을 선택한 것이다.

## 6. 재현성 계약

다음 값은 `model_config`, context manifest, strict EventClusterManifest와 audit에 결속된다.

```text
event clustering version
embedding provider/model identity
embedding batch size
semantic similarity threshold
max semantic variants
open-world cluster batch size
open-world hard prompt char budget
novelty batch size
embedding provider/fallback status
```

`run_id` seed에는 설정뿐 아니라 실제 runtime clustering result hash가 들어간다. 동일 provider가
일시적으로 실패해 deterministic embedding fallback을 사용하면 다른 run ID가 생성되므로 기존
checkpoint를 덮어쓰지 않는다. result hash는 모든 cluster의 row/event/source ID와 signature,
embedding status를 포함한다.

## 7. 감사 계층

세 경로가 Phase 1 artifact를 독립적으로 검사한다.

```text
CLI context inspect
provenance audit
production diagnostics
```

검사 항목:

```text
artifact path와 SHA
Pydantic strict contract
원본 CSV row identity
row↔cluster lineage
material cluster↔Pass 0 finding 1:1
OpenWorld v2 schema/prompt version/hash
embedding method/status/config/result hash
zero-material day의 analysis_batch_count=0
member excerpt count와 hash
```

Phase 1 marker는 `model_config.event_clustering_version`과 current cluster method 중 하나로
판별한다. summary 문자열만 바꿔 strict audit를 우회할 수 없다. 반면 기존 legacy run은 새
member/finding 필드를 강제로 요구하지 않아 과거 context audit 호환성을 유지한다.

## 8. 외부 감사에서 발견해 수정한 문제

```text
real LLM 빈 응답을 heuristic으로 채우던 semantic laundering
체결/해지, 결정/철회 같은 반대 사건 과병합
plain 속보/단독/종합 prefix 때문에 issuer를 잘못 읽던 문제
공백 포함 issuer와 거래상대가 다른 사건 과병합
공통 연도가 다른 계약 금액 충돌을 가리던 문제
1조원/10000억원 동치 미인식
본문 4KB 뒤 차이를 exact duplicate로 잃던 문제
cluster 수만 제한하고 실제 prompt 크기를 제한하지 않던 문제
strict coverage artifact 삭제·event ID 변조가 감사 통과하던 문제
설정·embedding fallback이 run ID에 결속되지 않던 문제
cluster ID 자기 선언만 맞으면 실제 분석 누락이 통과하던 문제
legacy run에 새 필드를 강제해 audit regression을 만들던 문제
diagnostics가 Pass 0 artifact를 직접 검사하지 않던 문제
```

## 9. 검증

Phase 1 targeted gate:

```text
ruff: PASS
mypy: 89 source files PASS
Phase 1 관련 pytest: 85 PASS
```

전체 repository gate도 통과했다.

```text
ruff: PASS
mypy: 89 source files PASS
pytest: 100% PASS
git diff --check: PASS
```

독립 감사 최종 판정:

```text
APPROVE
남은 P0/P1 없음
```

잔여 위험은 Phase 1 완료를 막는 결함이 아니라 후속 성능 범위다. 현재 보수 정책은 cutoff-safe
cluster를 대부분 material로 보내므로 real-provider 호출량이 클 수 있고 complete-link는 O(n²)다.
실제 50k/200k/600k corpus와 대형 일일 뉴스 부하는 Phase 8에서 측정·최적화한다.

주요 edge test:

```text
1001개 뉴스 row bounded embedding
25 material cluster 3개 Pass 0 batch
모든 행이 audit-only인 zero-batch day
embedding provider failure와 fallback
같은/다른 금액·단위·issuer·predicate·state·counterparty
long body tail exact fingerprint
hard prompt budget fail-closed
wrong/missing LLM coverage와 blank semantic output
runtime fallback/config 변경 시 run ID 변화
CLI/provenance context inspection
```

## 10. Phase 1이 하지 않는 일

Phase 1은 오늘 뉴스를 누락 없이 사건 단위로 읽게 만든 단계다. 아직 다음은 구현하지 않았다.

```text
60만 record online 전수 scan 제거
coverage path와 reasoning payload 물리 분리
production ANN/FTS index
memory cell과 population cube
diverse representative/adaptive drill-down
real provider production 승격
```

이 작업은 각각 Phase 2~9에서 진행한다.
