# Semantic Brain Upgrade Baseline

## 판정

현재 staging은 원료 보존, record 회계, warehouse, 실임베딩 memory index까지 완전하다. 그러나 offline compiler가 실제 payload를 map LLM에 노출한 범위는 `2,688 / 823,279`이므로 semantic exposure는 부분적이다.

이 baseline은 이후 retrieval/compiler 비교의 immutable 기준이며 기존 staging artifact를 수정하지 않는다.

## 기준 식별자

| 항목 | 값 |
|---|---|
| staging project | `production/staging/P9IMPORT-3D770A7DD72457C97098/project` |
| staging build commit | `0eea166b268ac80dd645e55bf32b142e8be0b443` |
| baseline freeze commit | `fc41f6ec6f2279778b5f7a970826e6d379900a40` |
| brain | `brain-08fe3aaaa3` |
| brain root | `1009852b70bd13c8ce4cc32fbfbf2ff52134ac377ef99b7a1bf04790f498b9c0` |
| memory snapshot | `MEMIDX-1e64a1b6e6ba7b07b799` |
| memory root | `80db0601f8ebc20e8c15b54729131505120c73ee0fdb3c00a1a779f078022a83` |
| warehouse root | `d830c76d0f3544a2c1bf3a39717bbf07046653fa12939f939b7204ae27079e10` |
| record corpus | `2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75` |
| compiler | `nslab.brain.llm_full.compiler.v7` |
| map-reduce | `nslab.brain.llm_full.map_reduce.v5` |
| LLM | `codex-oauth / gpt-5.6-sol / xhigh` |

## 수치

| 항목 | 수치 |
|---|---:|
| records | 823,279 |
| training eligible | 524,948 |
| deterministic record claims | 256,783 |
| offline payload exposed | 2,688 |
| offline payload not exposed | 820,591 |
| claim referenced | 256,783 |
| claim unreferenced | 566,496 |
| rare reasoning payload not exposed | 247,682 |
| artifact files | 92,682 |
| artifact bytes | 84,810,725,586 |

## 보존 정책

- Gold/repaired 원료, record store, warehouse와 기존 MiniLM embedding을 재사용한다.
- 전체 re-import와 전체 re-embedding을 수행하지 않는다.
- `brain-08fe3aaaa3`과 `MEMIDX-1e64a1b6e6ba7b07b799`을 덮어쓰지 않는다.
- 검증 중 생성물은 별도 evaluation/staging root에만 기록한다.
- historical holdout, live shadow, 외부 감사 전에는 production pointer를 만들지 않는다.
