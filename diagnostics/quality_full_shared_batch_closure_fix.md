# QUALITY_FULL Shared Batch Closure Fix

## 관찰

안전 3-case 평가의 두 번째 날짜 `2026-03-24`에는 material cluster가
1,567개였다. 기존 shared map batching은 설정의
`open_world_cluster_batch_size=12`를 적용하지 않고 120,000 character
예산만 사용해 첫 호출에 28개 cluster를 넣었다. provider 응답은 source와
analyzed identity 28개를 반환했지만 `cluster_findings`는 25개만 반환했다.
엄격한 coverage 검증이 이를 거부했고 shared context, prediction, score는
생성되지 않았다.

## 수정

- shared map은 character budget과 configured cluster limit을 모두 지킨다.
- shared novelty도 character budget과 configured cluster limit을 모두 지킨다.
- 두 숫자 상한과 `CONTEXT_CHARS_AND_CONFIGURED_CLUSTER_LIMIT.v2` 정책을
  lookup identity에 포함한다.
- `ok`가 아닌 provider checkpoint는 변조된 성공값처럼 재생하지 않고 live
  retry 대상으로 분류한다.
- 모든 cluster는 계속 정확히 한 번 coverage되며 first-N과 silent
  truncation은 허용하지 않는다.

## 새 호출 topology

실제 cluster capsule과 prompt renderer로 재계산한 base 호출 수는 다음과
같다. reduce 호출은 map output 크기가 확정돼야 계산된다.

| 날짜 | material | map | novelty |
| --- | ---: | ---: | ---: |
| 2026-01-02 | 478 | 40 | 40 |
| 2026-03-24 | 1,567 | 131 | 132 |

첫 날짜 map prompt는 28,612~69,804 characters, novelty prompt는
44,299~103,612 characters다. 두 번째 날짜는 각각 24,478~86,625와
38,535~119,149 characters다.

## Provider 복구

일시적으로 workspace credit가 소진돼
`LLMCKPT-c13f15fff4839840`이 error 상태로 남았다. 복구 로직 보강 후 동일
checkpoint identity를 live 재시도했고 `shared_open_world_map.batch_0003`이
retry 0, prompt 65,087 tokens, completion 7,450 tokens의 `ok` 상태로
교체됐다.

## 검증과 판정

커밋은 `fd5eefe`, `6ccb394`, `90780ac`이며 원격 브랜치에 push됐다.
`ruff`, `mypy` 135 source files, 전체 `pytest` 1,849개가 통과했다. 실제
3-case prediction과 score는 계속 진행 중이므로 predictive quality는
`NOT_EVALUATED`, production activation은 `HOLD`다.

## 도메인 검증 체크포인트 복구

두 번째 날짜의 새 map 배치 28개가 도메인 검증까지 통과한 뒤,
`shared_open_world_map.batch_0029` 응답은 구조화 스키마에는 맞았지만 배정된
cluster coverage와 일치하지 않았다. 기존 순서는 provider 응답을 `ok`로 저장한 뒤
coverage를 검사했기 때문에 단순 재시작 시 같은 잘못된 체크포인트를 반복 재생할 수
있었다.

복구 후에는 인증된 기존 체크포인트와 새 provider 응답 모두 map, reduce, novelty의
도메인 정규화·coverage 검증을 통과해야 재사용된다. 도메인 검증 실패는 bounded
provider retry에 포함되고, 성공한 교체 응답의 checkpoint와 trace에 `retries`와
`retry_errors`가 보존된다. 이미 도메인 검증을 통과한 앞선 배치는 다시 호출하지
않는다.

중간 build 재개와 pre-seal 재개가 기존 immutable node bytes를 유지하는 회귀도 함께
통과했다. predictive quality는 아직 `NOT_EVALUATED`, production activation은 계속
`HOLD`다.
