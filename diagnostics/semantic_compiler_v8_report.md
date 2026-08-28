# Semantic Compiler v8

Status: `NOT_RUN_GATE_HOLD`.

Phase C는 retrieval-only V0/V1 runtime gate를 통과한 뒤에만 시작하도록 사전 등록됐다. 실제 OAuth probe에서는 V0 한 건도 끝나기 전에 공통 단계가 일일 latency 예산을 64.425배 초과했고, known-relevance label도 없어 runtime gate를 닫을 수 없었다.

따라서 semantic unit, leaf/reduce tree, `SynthesizedMechanismClaim`, V2 evaluation brain은 만들지 않았다. 이를 0건으로 표현하지 않고 `unavailable / not run`으로 유지한다. 기존 evaluation brain `brain-b0e59ca379`과 production baseline은 보존됐다.
