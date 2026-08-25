# 연구 번들 repair 재개 체크리스트

이 문서는 남은 연구자료가 준비됐을 때 기존 repair 결과를 훼손하지 않고 작업을
이어가기 위한 운영 절차다. 상세 설계 계약은
[`docs/codex_goal_repair_only.md`](../codex_goal_repair_only.md), 실제 구현은
`src/news_scalping_lab/tools/sequential_repair.py`와
`src/news_scalping_lab/tools/repair_research_bundle.py`가 기준이다.

## 핵심 원칙

- 원본 MD는 수정하지 않고 `research/inbox/bundles/raw/`에 보존한다.
- 한 번에 원본 한 파일만 처리한다.
- 없는 source, fact, inference, ticker, outcome을 만들지 않는다.
- 알 수 없는 버전이나 record type은 삭제하지 않고 보존 또는 격리한다.
- provenance가 닫히지 않은 record는 삭제하지 않고 학습 eligibility와 weight를 내린다.
- repair는 production store에 import하지 않는다. 모든 import와 deep audit는 임시 store에서 수행한다.
- 기존 결과는 engine digest 변경만으로 자동 재실행하지 않는다.

## 남은 자료 전체 입고

남은 연구자료가 여러 번 나뉘어 도착하더라도 최종 일괄 repair를 시작하기 전에는
입고가 끝났다는 운영 확인을 먼저 받는다. 그 시점에 원본 전체를
`research/inbox/bundles/raw/`에 복사하고 다음 값을 고정한다.

```text
상대경로
source SHA-256
byte size
거래일
총 원본 파일 수
```

원본과 기존 `sequential_repair_manifest.v2.jsonl`은 별도 저장소에도 백업한다.
동일 source SHA는 중복 실행하지 않고, 경로만 같고 SHA가 달라진 파일은 덮어쓰지
않는다. 어느 원본이 추가·교체됐는지 먼저 확인한 뒤 별도 source로 보존한다.

최종 완전성 검사는 날짜 목록이나 파일명 패턴으로 세지 않는다. 같은 날짜의 두 번째
episode, 비거래일 receipt, 이름이 다른 보존 파일이 날짜 기반 집계에서 빠질 수 있기
때문이다. 입고 root를 재귀 열거해 모든 실제 파일의 SHA-256을 다시 계산하고, 각 SHA가
manifest의 최신 terminal row와 정확히 일대일 대응하는지 확인한다. `REPAIRED_PASS`는
게시된 repaired 파일의 byte/SHA도 다시 계산하고, 나머지는 허용된 보존 상태와 명시적
classification reason을 확인한다.

자료는 한 번에 모두 입고할 수 있지만 repair 실행 단위는 계속 **원본 한 파일**이다.
전체 자료에 대해 한 명령으로 병렬 repair하거나 blocker를 건너뛰지 않는다. 중간에
작업이 중단되면 기본 resume 동작으로 다음 미처리 파일부터 이어가며, 이미
`REPAIRED_PASS`로 닫힌 source는 다시 실행하지 않는다.

## 새 자료 처리

1. 새 원본을 `research/inbox/bundles/raw/` 아래 적절한 연도 위치에 둔다.
2. 원본과 현재 manifest를 별도 저장소에 백업한다. raw/repaired/.work는 Git 비추적 데이터다.
3. 아래 명령으로 다음 미처리 원본 한 파일만 처리한다.

```powershell
python -m news_scalping_lab.tools.sequential_repair --max-files 1
```

특정 새 파일만 처리해야 하면 경로를 명시한다.

```powershell
python -m news_scalping_lab.tools.sequential_repair `
  --source-path <RAW_BUNDLE_PATH> `
  --max-files 1
```

정상 작업에서는 `--continue-after-blocker`, `--no-resume`, 다중 파일 실행을 사용하지 않는다.

한 달의 마지막 거래일 파일까지 닫히면 다음 달로 넘어가기 전에 별도 읽기 전용
월간 감사를 수행한다. 감사자는 해당 월의 manifest, repaired 산출물, lineage,
격리 import/deep-audit 보고서만 검토하며 repair나 production import를 실행하지 않는다.
범용 repair 결함이 확인된 경우에만 영향받은 source SHA/date를 명시해 재처리한다.

## 결과 판정

- `REPAIRED_PASS`와 `ready_for_import=true`: repair 완료.
- `PRESERVED_PARTIAL_NOT_CURRENT_GOLD`: 원본에 필요한 근거 또는 모집단이 없어 보존.
- `PRESERVED_SOURCE_PAYLOAD_ABSENT`: 원본에 실제 machine payload가 없어 보존.
- `DEFERRED_NON_TRADING`: 비거래일 자료로 별도 보존.
- `PARTIAL_PRICE_SOURCE_MISSING`: 필요한 가격 원천이 준비될 때까지 보류.
- `ADAPTER_REQUIRED` 또는 `FATAL_INPUT_FAILURE`: 다음 파일로 넘어가지 말고 범용 parser/repair 규칙을 수정한다.

파일별 근거는 다음 위치에 남는다.

```text
research/inbox/bundles/.work/<source_sha256>/source_census.json
research/inbox/bundles/.work/<source_sha256>/engine_manifest.json
research/inbox/bundles/.work/<source_sha256>/repair_summary_a.json
research/inbox/bundles/.work/<source_sha256>/repair_summary_b.json
research/inbox/bundles/.work/<source_sha256>/isolated_import_audit.json
research/inbox/bundles/.work/<source_sha256>/mechanical_quality_gate.json
research/inbox/bundles/.work/<source_sha256>/mechanical_lineage.jsonl
research/inbox/bundles/.work/<source_sha256>/sequential_result.json
research/inbox/bundles/repaired/sequential_repair_manifest.v2.jsonl
```

`REPAIRED_PASS`는 두 repair 결과의 byte/SHA 일치, record 손실 0, import loss 0,
typed payload 오류 0, provenance/eligibility 오류 0, 격리 import/deep audit 통과,
production store 무변경을 모두 요구한다.

## 기존 파일 재처리

범용 규칙을 수정한 뒤 영향을 받은 기존 source만 명시적으로 재처리한다.

```powershell
python -m news_scalping_lab.tools.sequential_repair `
  --source-date YYYYMMDD `
  --retry-existing `
  --max-files 1
```

재처리 전에 관련 회귀 테스트를 추가하고, 같은 구조 계열의 기존 fixture도 함께
검증한다. 날짜, 종목, 테마별 예외를 production 코드에 추가해서는 안 된다.

## 범용 repair 근거를 남기는 방법

구형 bundle 표기 차이를 보강할 때는 원본 선언을 복사하는 것만으로 끝내지 않고 다음
증명 규칙을 코드 주석, 파생 receipt, 회귀 테스트에 함께 남긴다.

- `matched_source_row_ids` 같은 source alias는 원본의 정확한 집합만 canonical provenance로 옮긴다.
- 방향이 있는 preference pair는 fact 집합뿐 아니라 원본 순서까지 같아야 같은 pair로 연결한다.
- 명시적 비학습 case는 0-weight로 보존하고, 누락된 사유는 source-declared ineligible로 기록한다.
- sealed pair 계약이 없는 pair는 원본이 eligible이라 해도 학습용으로 승격하지 않는다.
- 누락 semantic witness는 기존 witness 직렬화 관례와 screening/ranking/fact/inference/review/source
  chain이 모두 유일하고 hash로 재검증될 때만 materialize한다.
- outcome을 보지 않는 post-seal 제거 receipt는 제거 대상의 semantic FAIL, 최종 제외, 순위와
  개수 불변을 모두 증명하고 receipt 안에 SHA-bound `validated_final_watchlist`를 실제 게시한
  경우에만 final relation surface로 인정한다. 내부 계산으로만 sealed 후보를 숨기지 않는다.
- legacy population 증가는 source와 repaired의 exact closure가 각각 닫히고, 추가 record가
  source case payload SHA와 derivation inputs로 전부 설명될 때만 허용한다.

어느 조건 하나라도 유일하지 않으면 추정해 채우지 않고 blocker 또는 보존 상태로 남긴다.

## 같은 episode를 선언한 상충 원본

월간 감사에서 서로 다른 source SHA가 같은 episode ID와 record ID를 선언하면서 record
payload가 다르다고 확인되면 어느 한쪽을 파일명이나 다운로드 순서로 권위본이라 추정하지
않는다. 두 source의 전체 SHA를 명시해 다음 명령을 실행한다.

```powershell
python -m news_scalping_lab.tools.sequential_repair `
  --quarantine-conflicting-source-sha <SOURCE_SHA_1> `
  --quarantine-conflicting-source-sha <SOURCE_SHA_2>
```

명령은 실제 repaired bundle에서 공유 episode와 상충 record를 다시 증명한 경우에만
동작한다. 모든 관련 repaired 파일을 `repaired/quarantined/<연도>/`로 옮기고 manifest를
`PRESERVED_PARTIAL_NOT_CURRENT_GOLD`, `ready_for_import=false`로 갱신하며
`.work/cross_source_conflicts/<conflict_id>/receipt.json`을 남긴다. 이후 명시적인
authoritative/supersession receipt 또는 서로 다른 episode임을 증명하는 원본 metadata가
오기 전에는 자동 병합, source-SHA namespace 재작성, 임의 권위본 선택을 하지 않는다.

## 배치 종료 후

```powershell
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```

새 repair 묶음의 source/repaired/quality-gate SHA와 상태 집계를 확인한 뒤에만 production
inventory를 다시 만든다. repair 완료와 production import/activation은 서로 다른 작업이다.

## 전체 repair 이후 production 전환

모든 입고 원본이 최종 상태를 갖고 월간 감사까지 닫힌 뒤 다음 순서로 전환한다.

```text
전체 source/repaired/quality-gate root 재계산
→ READY_FOR_IMPORT 및 보존·격리 상태 집계
→ production inventory 생성·검사·봉인
→ stage-import zero-write preflight
→ 격리된 production batch import 실행 및 receipt 검사
→ llm-full brain 재구축·deep audit
→ production memory index와 warehouse 재구축·검사
→ strict production doctor/readiness
→ release finalize·inspect·activate
```

구체적인 import와 활성화 명령은
[`docs/0813/production_activation_runbook.md`](../0813/production_activation_runbook.md)를
따른다. repair 단계에서는 위 production 명령을 미리 실행하지 않는다. 새 자료가
나중에 추가되면 변경된 원본만 같은 절차로 repair하고 새 inventory/release를 만들며,
기존 정상 원본을 매번 다시 import하거나 repair하지 않는다.
