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
`ruff`, `mypy` 135 source files, 전체 `pytest` 1,851개가 통과했다. 실제
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

### 실제 실패 내용

원본 checkpoint와 deterministic batch ledger를 대조한 결과, source cluster ID 12개와
`cluster_findings` 12개는 개수·값·순서가 모두 정확했다. 실패한 필드는 중복 envelope인
`analyzed_cluster_ids` 한 항목의 마지막 문자 `d`가 빠진 것뿐이었다. 따라서 실제 의미
coverage 누락은 없었다.

정규화는 source ID와 finding ID가 정확하고, uncovered가 비어 있으며, analyzed echo의
개수와 유일성이 배치 개수와 같을 때만 analyzed identity를 deterministic ledger로
재결속한다. 원본 echo는
`b20522a5852f53bcfc71413f83c6309a6197ee63db3cb9601b91a769eff6b3f0`으로
notes에 commitment된다. finding 하나라도 빠지거나 순서가 다르면 계속 fail-closed다.

### Identity drift 복구

첫 수정에서 formatter가 `_map_batches`와 `_novelty_batches`의 소스 표현을 바꿔
prompt renderer fingerprint가 `e8f07bf7…4e06`으로 달라졌다. 이 때문에 미봉인 partial
context `SHAREDCTX-323b4abbef7f79580f10`이 생기고 첫 날짜 novelty 13회가 중복
호출됐다. 이를 발견한 즉시 PID `59256` 트리만 중단했으며 prediction·score·production
변경은 없었다.

두 renderer 함수의 소스 표현을 원래대로 복구한 뒤 현재와 baseline의
`prompt_renderer_sha256`는 모두 `b46b58e5…ad0a`로 일치한다. 위 partial context와
중복 호출은 품질 점수에 사용하지 않는다.
