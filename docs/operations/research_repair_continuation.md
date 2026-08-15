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

## 배치 종료 후

```powershell
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```

새 repair 묶음의 source/repaired/quality-gate SHA와 상태 집계를 확인한 뒤에만 production
inventory를 다시 만든다. repair 완료와 production import/activation은 서로 다른 작업이다.
