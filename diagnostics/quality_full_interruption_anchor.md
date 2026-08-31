# QUALITY_FULL Interruption Anchor

## 목적

Codex OAuth 사용량 한도로 PR126 정식 3-case 평가가 중단된 시점의 durable
artifact를 고정한다. 재개 전후에 이 anchor와 로컬 파일을 비교하면 완료된 shared
분석이 변경되거나 다시 계산되지 않았음을 확인할 수 있다.

## 고정 상태

- repository commit: `764006c`
- safe selection: `QSEL-19b3c80ba392db8564c9`
- selection SHA-256: `bfcc21e4…3f9c`
- 첫 날짜 context: `SHAREDCTX-e1223be1dff5739d872c`, sealed
- 두 번째 날짜 context: `SHAREDCTX-43526498ebe9339063b4`, partial
- map: 131/131, finding 1,567/1,567, ordered coverage exact
- reduce: level 1은 9/9, level 2는 1/3
- novelty·V0/V1 prediction·outcome open·score: 미시작
- production mutation: 0

## Artifact Roots

- 두 번째 context input root: `01cb01d9…973f`
- map 131개 root: `471a7f04…a0e4`
- reduce 10개 root: `98f66314…c20e`
- 마지막 성공 checkpoint: `LLMCKPT-5fab122ab539a1d1`, `94d66bb7…d800`
- 실패 checkpoint: `LLMCKPT-4220c9a95859f54b`, `56d09727…f32e`
- stderr log: `1408eabe…44d2`

Root는 artifact를 POSIX 상대 경로로 정렬한 뒤 각 행을 `path`, `sha256`,
`size_bytes`로 만들고, 공백 없는 UTF-8 canonical JSON의 SHA-256으로 계산했다.

## 재개 규칙

동일 코드·설정·blind selection으로 `predict-runtime-variants`를 다시 실행한다.
authenticated `ok` checkpoint는 재생하고 `error` checkpoint는 재사용하지 않는다.
따라서 다음 live 호출은
`shared_open_world_reduce.level_02.batch_0002`여야 한다. 재개 전에 위 root가
동일한지 확인하고, 재개 후 새 artifact만 추가됐는지 확인한다.

현재 판정은 `NOT_EVALUATED`, production activation은 `HOLD`다.
