# 첫 해석부터 두뇌를 사용하는 일일 경로 수정

작성일: 2026-09-07

사용자 요구는 저장된 연구 지식이 있는 상태에서 오늘 뉴스를 해석하는 것이다.
기존 구현은 첫 LLM 호출에 현재 뉴스와 D-1만 주고, 그 응답으로 과거 지식을
검색했다. 첫 가설이 틀리면 기억 검색도 그 가설에 치우칠 수 있었다.

## 변경된 실행 순서

1. CSV를 cutoff-safe 사건으로 묶는다.
2. 뉴스 제목·술어·기업·상대방·수치·확정성에서 직접 검색 query를 만든다.
3. 기존 package의 공통/분야별 합성 지식을 읽고 관련 capsule/claim을 조회한다.
4. GPT 한 번이 그 지식과 현재 뉴스를 함께 읽어 해석·검토·최종 판단한다.
5. 같은 응답의 사건 coverage, 출처, cutoff를 검증하고 답변을 저장한다.

과거 이름의 존재 여부는 새 후보를 허용하거나 차단하는 기준이 아니다.
사용자가 명시한 제품은 두뇌+GPT가 뉴스를 함께 읽고 답하는 흐름이다.
LLM 호출 수는 정상 1회, 형식 보정이 필요한 경우에만 최대 2회다.
별도 1차 해석, 원문 batch별 LLM 호출, 두 번째 근거 모집은 없다.
기존 정상 2회 계약은 이번 사용자 정정으로 대체한다.

## 증거와 재사용

- 문맥을 첫 호출 전에 저장한다. 뉴스 hash, package root, build cutoff와 결속한다.
- 공통/분야별 지식의 원본 경로와 SHA-256을 문맥에 남긴다.
- 두뇌 누락, 내용 hash 변경, 잘못된 뉴스 결속, 미래 지식은 LLM 호출 전에 거부한다.
- 일일 manifest에 첫 호출 전 두뇌 장전 여부와 검색 기준을 기록한다.
- daily schema/prompt/architecture를 v2로 변경한다.
- offline compiler v5, 합성 prompt/schema, package 생성 규약은 유지한다.
- 기존 의미 합성 checkpoint를 재사용하며 repair/import/전수 embedding을 재실행하지 않는다.

## 검증 상태

관련 회귀 테스트 24개, Ruff, Mypy(138개 source file)는 통과했다.
전체 pytest는 345.58초에 1,886개 통과, 문서 형식 규칙 1개 실패였다.
추가 지침을 Product Intent 구역으로 옮긴 뒤 해당 테스트와 `pytest --lf`가
통과했다. 이 수정에서 production code나 테스트 기준을 변경하지 않았다.
전체 suite를 두 번째로 실행한 것은 아니며, 남은 확인된 실패는 0개다.
실제 production package의
예측 품질이나 일일 소요시간이 입증됐다는 뜻은 아니다. 이번 수정은 첫 호출의
지식 입력 순서에 대한 것이며, 검색 누락·사건별 지식 배분의 최적성을 입증하지 않는다.

AST 비교로 offline compiler class와 모든 version 상수, 합성용 contract가 기존
HEAD와 동일함을 확인했다. 변경된 것은 일일 reader·문맥·응답·호출 경로다.
정상 1회 호출 감사는 `diagnostics/daily_llm_call_graph_single_call.json`에 기록한다.
기존 `daily_llm_call_graph_after.json`은 당시 2회 구현의 감사 증거로 보존한다.
