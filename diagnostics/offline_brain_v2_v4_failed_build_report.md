# Offline Semantic Brain V2 v4 full-build 실패 감사

## 판정

2026-09-02의 첫 823,279-record full build는 local 전수 geometry와
52,644개 semantic unit 생성을 마친 뒤 장문 payload map 단계에서
fail-closed로 종료됐다. BrainPackage는 생성되지 않았고 production pointer도
변경되지 않았다.

## 실제 원인

v4의 `LongPayloadChunkDigest`는 의미 요약뿐 아니라 다음 원장 필드도 LLM이
그대로 복사하도록 요구했다.

```text
semantic_unit_id
record_id
chunk_index / chunk_count
document_sha256 / chunk_sha256
```

`LLMCKPT-26cc923defd86822`은 JSON schema와 8개 chunk-ID closure를 통과했지만,
세 record의 document/chunk SHA를 이웃 record 사이에서 순환 이동시켰다.
compiler가 원본 memory DB와 비교해 이를 검출했고
`long payload digest mutated source identity`로 종료한 것은 올바른 동작이다.

## 보존과 무효화

동시성 4로 실행되는 동안 v4 checkpoint 11개, digest 55개가 저장됐다.
파일은 삭제하지 않고 forensic evidence로 보존한다. 그러나 v4 응답 계약의
ancestor이므로 새 build나 production package에는 한 건도 재사용하지 않는다.

## v5 수정

v5에서 LLM은 `chunk_id`와 의미 필드만 작성한다. semantic unit, record,
순번, document SHA, chunk SHA는 immutable chunk input ledger에서 compiler가
결정론적으로 붙인다. compiler version과 prompt version을 모두 올려 v4
checkpoint key와 물리적으로 분리했다.

이 수정은 연구 원료를 줄이거나 payload를 생략하는 변경이 아니다. LLM이
읽는 원문과 의미 출력은 그대로 유지하면서, 원본 신원 필드의 작성 권한만
LLM에서 원장 코드로 회수한다.
