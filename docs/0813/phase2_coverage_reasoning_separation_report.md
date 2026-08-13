# Phase 2 전수 Coverage와 Online Reasoning 분리 보고서

상태: 구현·전수 검증·독립감사 승인 완료

## 1. 목적

전체 연구 record는 매일 빠짐없이 감사하되, 전수 record 본문과 전수 ID 목록을
LLM 판단 prompt에 넣지 않는다.

```text
Coverage path
  accepted record 전체 envelope SHA 봉인
  cutoff-safe available record ID와 SHA 봉인
  future/missing/unexpected/duplicate count
  immutable MemoryCoverageManifest

Reasoning path
  current news
  open-world analysis
  bounded retrieval 결과
  반례와 검증 결과
  category brain reference
```

## 2. 구현 결과

### 2.1 Streaming MemoryCoverageManifest

`context/memory_coverage.py`를 추가했다.

- accepted record 전체를 한 번만 순회한다.
- record ID, `available_from`, record type, evidence phase, eligibility와 전체
  envelope SHA를 JSONL로 봉인한다.
- available ID 목록과 available record hash manifest를 별도 content-addressed
  artifact로 저장한다.
- 현재 뉴스, LLM model config와 무관하므로 corpus와 cutoff가 같으면 재사용한다.
- 임시 파일에 write/flush/fsync 후 같은 volume에서 `os.replace`한다.
- 중단된 partial은 최종 artifact로 채택되지 않는다.

`BrainRecordStore.iter_records()`를 추가해 60만 record를 Python list로 모두
보유하지 않고 coverage artifact를 생성한다.

### 2.2 Production sweep 경로

새 일일 분석은 `emit_legacy_contributions=False`로 실행한다.

- `memory_sweep_contribution` 본문을 생성하지 않는다.
- `record_memory_sweep_contribution` 본문을 생성하지 않는다.
- accepted/available/swept count와 coverage evidence는 유지한다.
- 기존 legacy sweep API와 legacy artifact 감사 코드는 읽기 호환을 위해 유지한다.

### 2.3 Final synthesis v2

새 production final contract는 `nslab.final_synthesis_context.v2`, prompt는
`synthesis.final.v2`다.

제거한 필드:

```text
all_shard_contributions
record_level_shard_contributions
memory_sweep_artifacts
record_sweep_artifacts
record_sweep_artifact_hashes
available_record_ids
training_eligible_available_record_ids
swept_record_ids
```

대체 필드:

```text
memory_coverage_manifest
  artifact_path
  sha256
  corpus_manifest_sha256
  accepted_record_count
  available_record_count
  training_eligible_available_record_count
  coverage_complete
  strict manifest
```

Shard brain도 원문을 final prompt에 넣지 않고 path/SHA/byte size reference만
전달한다. 기존 v1 final context와 bundle은 계속 읽을 수 있다.

### 2.4 Hard token gate

`limits.final_synthesis_token_budget=80000`을 추가했다. 최종 prompt가 예산을
넘으면 LLM 호출 전에 `FinalSynthesisBudgetError`로 종료한다. 조용한 truncate는
하지 않는다.

### 2.5 독립 감사 경로

CLI context inspection, provenance audit, lookahead audit가 다음을 독립 검증한다.

- coverage manifest path/hash/schema/run/cutoff
- accepted/available/future count
- available IDs와 record hash rows의 동일성
- accepted manifest가 available manifest를 포함하는지
- full envelope SHA와 현재 record store의 결속
- final v2에 금지된 exhaustive payload가 없는지
- analysis bundle export/import의 v1/v2 호환성

## 3. 성능 실측

명령:

```powershell
python -m news_scalping_lab.tools.profile_memory_coverage --counts 50000 200000 600000
```

Windows/Python 3.14, synthetic full-envelope hash 기준:

| Record | Available | 시간 | 처리량 | Peak memory | Manifest |
|---:|---:|---:|---:|---:|---:|
| 50,000 | 45,000 | 4.666초 | 10,716.5/s | 7.35 MiB | 1,449 bytes |
| 200,000 | 180,000 | 24.154초 | 8,280.2/s | 22.80 MiB | 1,456 bytes |
| 600,000 | 540,000 | 68.697초 | 8,734.0/s | 56.32 MiB | 1,456 bytes |

위 수치는 Windows/Python 3.14에서 단일 실행한 값이다. 독립감사 재실행은
600,000건 66.102초로 측정되어, 머신 부하에 따른 실행시간 편차는 있으나
메모리 상한과 최종 manifest 크기는 동일하게 재현됐다.

Coverage artifact의 상세 JSONL은 record 수에 선형으로 증가하지만 final reasoning
payload에 들어가는 manifest는 약 1.5 KB로 유지됐다.

## 4. 검증

내부 gate:

```text
ruff: PASS
mypy: PASS (90 source files)
pytest: PASS (1,253 tests)
50k/200k/600k streaming profile: PASS
independent audit: APPROVE (P0/P1 0건)
```

회귀 검증:

- cutoff 이후 record는 available artifact에 포함되지 않음
- provenance만 바뀐 동일 payload record도 corpus hash 변경
- 같은 corpus의 두 번째 실행은 coverage cache hit
- final v2에 전수 contribution과 전수 ID 목록 없음
- final prompt token budget 초과 시 fail-closed
- coverage manifest 변조 시 CLI/provenance/lookahead 실패
- analysis bundle v1/v2 read compatibility 유지

## 5. 남은 범위

Phase 2는 전체 record가 학습 판단에 어떤 방향으로 쓰이는지는 바꾸지 않는다.
다음 단계에서 각각 구현한다.

```text
Phase 3: polarity / eligibility / label quality / routing 분리
Phase 4: production ANN + FTS retrieval
Phase 5: selected memory cell 전체 모집단 통계
Phase 6: diverse representatives와 adaptive drill-down
```
