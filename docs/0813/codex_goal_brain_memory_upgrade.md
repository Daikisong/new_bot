# Codex Goal: 장기기억 두뇌 활용 계층 구현

아래 두 문서를 구현 계약으로 사용한다.

```text
docs/0813/external_brain_design_feedback.md
docs/0813/brain_memory_implementation_phases.md
```

## 목표

repaired 연구 record 전체를 보존·감사하면서, 현재 뉴스와 관련된 전체 관측 모집단 통계와
다양한 성공·실패·반례를 production token/latency 안에서 사용하는 장기기억 두뇌를 만든다.

## 실행 규칙

1. 현재 요청에서 명시된 phase 하나만 구현한다.
2. 다음 phase 코드를 미리 활성화하지 않는다.
3. phase 시작 전 실제 코드와 문서 계약을 line-by-line 대조한다.
4. 기존 open-world first, BLIND, cutoff, provenance, unknown preservation 계약을 약화하지 않는다.
5. 날짜·종목·ticker·theme·region·beneficiary를 production 코드에 하드코딩하지 않는다.
6. exact keyword를 candidate 생성·차단 gate로 사용하지 않는다.
7. schema와 실패 상태를 구현한 뒤 정상 경로를 구현한다.
8. unit, integration, adversarial, performance regression을 추가한다.
9. phase 종료 전 수정 호출부와 최종 synthesis/audit 경로까지 변수·논리를 대조한다.
10. `ruff`, `mypy`, 전체 `pytest`를 통과한다.
11. 실제 corpus에는 shadow/read-only 검증만 수행한다.
12. Phase 9 전에는 1,127 repaired bundle을 production store에 import하지 않는다.
13. production brain pointer와 real provider를 Phase 9 전에는 변경하지 않는다.
14. 완료하지 못한 항목을 통과로 표시하지 않는다.

## 금지

```text
top-3 숫자만 크게 올려 해결했다고 주장
ANN top-K를 관측 모집단의 통계 분모로 사용
모든 record 원문을 final LLM prompt에 넣음
training_eligible를 positive polarity로 사용
outcome missing을 negative로 계산
historical replay에 최신 cell/index/brain 사용
LLM이 count/rate/percentile을 자의적으로 계산
coverage가 없는 record를 조용히 버림
```

## phase 실행 형식

각 phase는 다음 결과를 남긴다.

```text
구현한 계약
수정 파일과 호출부
새 schema/version
추가 테스트
실제 corpus shadow 결과
성능 수치
통과한 gate
남은 blocker
다음 phase 시작 가능 여부
```

## 현재 시작점

첫 구현은 Phase 0이다.

```text
Phase 0: 기준선·계약·corpus profile
```

Phase 0 완료 후 사용자에게 결과를 보고하고 Phase 1 시작 승인을 기다린다.
