# 신규 Goal 복구 실행 기록: 2026-10-04

목표: `docs/operations/codex_goal_finish_brain_and_daily_csv.md`.
이 기록은 중간 작업 증거이며 두뇌 완성/백테스트 통과/production 활성화 보고가 아니다.
아래의 최초 복구·검증·당시 다음 작업은 WAL 원인 규명 전의 historical sequence다. 현재 실행 상태와
다음 작업은 마지막 `후속 WAL 복구와 최신 상태` 절 및 canonical goal 문서를 기준으로 한다.

## 실행 중단과 보존

사용자 재진행 의사에 따라 S0의 현행 실행 확인과 보존을 시작했다.
수정 worktree는 `C:\Users\eorb9\projects\news_bot_resume_clean_7198b6b`,
기준 HEAD는 `b150a77c8967bc15584d117cc1d48c0b5ca78063`이다.
기존 6개 미커밋 checkpoint 재사용/provenance 변경을 보존했다.

종료 대상은 PID 59528의 절대 command line으로 확인한 NSLAB `brain build-offline`이다.
source는 `news_bot/production/staging/P9IMPORT-3D770A7DD72457C97098/project`,
output은 compiler worktree의 `brain/packages/offline-v5-gpt-6.1-sol-high-20261004`였다.
당시 하위 프로세스는 conhost PID 76148 하나였고 살아 있는 Codex 호출은 없었다.
보호된 Bithumb root와 일치하지 않음을 확인하고 해당 Python만 종료했다.

종료 전 working set은 29,101,817,856 bytes, private memory는 31,460,311,040 bytes였다.
18:10 오류 이후 새 결과가 없는 상태였다. 이 관측만으로 메모리 증가의 정확한 원인을
확정하지 않는다. 오류 기록/worker 취소 변경과 메모리 회귀 검증은 별도로 수행한다.

첫 종료 직후 확인은 `Compiler still visible after stop`이었다. 같은 PID를 다시 조회해
18:54:34 KST에 Python과 conhost가 모두 사라졌음을 확인했다. 다른 프로세스는 제어하지 않았다.
원료, DB, checkpoint 파일을 삭제하거나 재생성하지 않았다. 새 합성 호출도 시작하지 않았다.

보존 DB의 종료 후 SHA-256:

| compile | byte 크기 | SHA-256 |
|---|---:|---|
| `OFFLINE-COMPILE-add3461bd175147e255d` | 2,126,524,416 | `0aecf3a7a22a17927c7feec3cf46a07bbdc5e67d804eab21e11cee27f40de7d6` |
| `OFFLINE-COMPILE-56eb0c0dd10edc4a44d0` | 247,214,080 | `a445497e88043155d19e9e2c537f69c0c5d5f6defbcf57793f6f4b0200019920` |

위 파일은 각 compile의 `brain/.work/<compile>/semantic_capsule_index.duckdb`다.
중단된 DB를 봉인된 package로 오인하지 않는다. 동일 compile 명령을 재실행하면 현행 build가
work DB를 지우므로 복구 경로가 완성되기 전 기존 명령을 재시작하지 않는다.

## 적용한 첫 수정

- `_reduce_category`: 층별 packing이 node 수를 줄이지 못하면 해당 층의 LLM 호출 전에
  `offline reduce cannot converge`로 종료한다.
- 통합 후 홀수 singleton 잔여는 그대로 다음 층에 전달한다. 같은 내용을 재요약하지 않는다.
- category 작업 중 실패가 발생하면 sibling task를 취소하고 회수한 뒤 예외를 전달한다.
  이 변경만으로 실행 중인 외부 subprocess 취소/메모리 문제가 전부 해결됐다고 주장하지 않는다.
- Codex 응답 validator 오류의 `ctx`에는 실제 `ValueError` 객체가 들어갈 수 있었다.
  오류 설명을 만들 때 `include_context=False`로 제외하여 원래 validation 메시지를 보존하고,
  보정 요청과 최종 실패가 `Object of type ValueError is not JSON serializable`에 가리지 않게 했다.

이 수정은 비수렴 재발 차단이다. 출처 ledger 분리, bounded 의미 응답, 유한 DAG 계획,
정확한 남은 호출 수, 복구용 capsule 재사용 인터페이스는 아직 미완료다.
따라서 기존 build 재개 허용이나 S1 전체 완료로 표시하지 않는다.

## 검증

- 실제 문제 크기 11,039 / 10,965 / 17,039 및 52,644 ID 조건에서 정체 검출,
  검출 시 LLM 호출 0 확인.
- singleton 잔여는 추가 호출 없이 전달하고, 정상 통합/최종 review만 호출함을 확인.
- long-payload 모델 validator 오류에서 1회 보정 후 원래 오류를 유지한 정상 실패 확인.
- 관련 단위 테스트 4개 파일: 114 passed, 172 warnings, 14.03초.
- 전체 Ruff: PASS.
- 전체 Mypy: PASS, 139 source files.
- 전체 pytest: PASS, 1,913 tests, 1,208 warnings, 297.09초.

경고는 주로 Python 3.14의 pytest-asyncio event-loop-policy deprecation이며,
감사팩 변조 검출 테스트에서 의도적으로 생성한 ZIP 중복 entry 경고도 있다.
이번 테스트는 mock/local 검증이며 실제 GPT 합성을 호출하지 않았다.

## 원료 기간 추가 확인

원본 `source_memory.records.trade_date`와 이전 DB의 assignment/capsule을 join했다.
823,279개 모두 배정 및 capsule에 연결됐으며 연도별 누락은 0이다.
범위는 2018-01-03~2026-06-19다. '꽉 찬 10년'이나 해당 기간 모든 거래일이
존재한다고 해석하지 않는다. 전체 원료의 연결과 의미 품질 검증도 구분한다.

| 연도 | 원본 = 배정 = capsule 연결 record 수 |
|---|---:|
| 2018 | 104,470 |
| 2019 | 97,670 |
| 2020 | 85,768 |
| 2021 | 82,808 |
| 2022 | 100,118 |
| 2023 | 102,292 |
| 2024 | 88,504 |
| 2025 | 97,678 |
| 2026 | 63,971 |

## 이전 조사 당시 다음 작업 (historical snapshot)

추가 read-only ancestry 감사에서 실제 기존 reduce 입력 bucket은 2,204개였다.
현재 capsule ID로 원래 `LEAF-BUCKET` identity를 재구성하고 모든 reduce/review child를
시간순으로 연결했으며 입력을 찾을 수 없는 checkpoint는 0개였다.
실제 다중-child 합치기는 201개, 단일-child 재요약은 765개였다.
이는 출처 연결 검증이며 201개의 내용 전부가 재사용 적합하다는 승인은 아니다.

| category | 다중-child 합치기 | 단일-child 재요약 |
|---|---:|---:|
| continuation | 16 | 0 |
| counterexamples | 19 | 0 |
| beneficiary_discovery | 20 | 0 |
| failure_modes | 24 | 252 |
| leader_selection | 20 | 0 |
| market_memory | 24 | 251 |
| single_event | 40 | 261 |
| world_model | 17 | 0 |
| theme_formation | 21 | 1 |

미완료 category의 마지막 실제 합치기는 failure_modes 9월 28일 23:23:12,
market_memory 9월 28일 23:00:35, single_event 9월 29일 01:34:25 KST였다.
이후의 반복 결과를 새 원료 반영으로 계산하지 않는다. 복구는 원 capsule과 검증된
정상 prefix를 기준으로 하고 반복 말단을 무조건 채택하지 않는다.

1. 전수 capsule 출처 목록을 로컬 ledger로 분리한 새 reduce 입출력 계약과 유한 DAG 구현.
2. 유효한 기존 capsule/정상 통합 prefix를 검증해 재사용할 복구 경로 구현.
3. 모델별 checkpoint 선택을 고정하고 실제 생성 모델 provenance를 보존.
4. 실제 보존 자산으로 전체 규모 로컬 계획을 생성하고 남은 작업 수를 보고.
5. 그 뒤에만 신규 합성, package 감사, 같은 일일 경로 백테스트와 운영 연결로 진행.

새 goal 전체는 미완료이며, 완료율이나 ETA를 과거 7,671회 분모로 계산하지 않는다.

## 후속 WAL 복구와 최신 상태

후속 CLI 시도에서는 다음 resume validation 오류가 발생했다:

```text
legacy resume database must contain one capsule per semantic unit: capsules=51908, units=52644
```

원인은 DuckDB resume copy가 `semantic_capsule_index.duckdb` main file만 복사하고, 아직 main file에
checkpoint되지 않은 `.wal` sidecar를 누락한 것이었다. main file만 연 사본은 51,908 capsules를
보였고 main+WAL의 원래 데이터베이스는 52,644 capsules/units였다. 이는 source research 누락이나
실제 capsule 736개 소실이 아니었다.

보존 source DB와 복구한 target DB의 attested 파일:

| 파일 | 크기 | SHA-256 |
|---|---:|---|
| `semantic_capsule_index.duckdb` | 2,126,524,416 bytes | `0aecf3a7a22a17927c7feec3cf46a07bbdc5e67d804eab21e11cee27f40de7d6` |
| `semantic_capsule_index.duckdb.wal` | 14,561,320 bytes | `dc9507249bb32c52290a5e854706ce01d51783edd8466d568874ed7218c745e2` |

수정된 worktree `news_bot_resume_clean_7198b6b`에서 다음을 적용했다:

- resume DB main file과 WAL을 각 SHA로 attestation하고 함께 안전 복사하는 경로.
- target이 이미 정확한 main file 사본이면 hash가 일치하는 WAL만 atomic하게 adopt하는 경로.
- WAL source가 있을 때 CLI가 기대 WAL SHA를 명시하게 하는 `--expected-resume-work-database-wal-sha256`.
- WAL copy/adopt, orphaned DuckDB WAL 보존, 실패 후 재개 회귀.

실제 target compile `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`에 WAL을 복구한 뒤 exact
`_validate_reusable_assignment_database` 검증이 통과했다. 검증 결과는 823,279 assignments,
823,279 distinct/source records, 52,644 centroids, 52,644 capsules, 52,644 capsule units였다.
source의 main/WAL hash는 복구 과정에서 변경되지 않았다. target은 source와 동일한 main/WAL hash를
갖는다. 임의 capsule 생성이나 completeness validator 완화는 하지 않았다.

2026-10-04 WAL 복구 시점의 검증:

- resume-focused tests: 3 passed.
- `python -m ruff check .`: PASS.
- `python -m mypy src/news_scalping_lab`: PASS, 139 source files.
- `python -m pytest`: 1,925 passed, 1,244 warnings, 304.01 seconds.
- 당시 WAL-aware test/build 수정은 local/compiler worktree에서 검증됐다. 이 문서 작성 시점에는
  production map-only가 실행 전이었고, final DAG/package, A/B/C, shadow, activation도 남아 있었다.
- 수정 전 map-only 시도는 validator 단계에서 map/reducer 전에 중단됐다. 그 실행의 trace/checkpoint
  전후 request count는 별도 봉인되지 않았으므로 과거 호출 수는 확정하지 않는다.

위 다음 작업 안내는 당시 시점의 historical status로 대체됐다. 현재 상태는 아래 2026-10-05 갱신을
기준으로 한다.

## 2026-10-05 map-only 실행과 reducer 재개 전 보강

기존 WAL-aware resume target을 사용해 map-only build를 실행했다. Compile ID는
`OFFLINE-COMPILE-0dd9198ac9ef79215ab1`, map receipt는
`brain/.work/OFFLINE-COMPILE-0dd9198ac9ef79215ab1/offline_map_plan_receipt.json`이다.
Receipt status는 `MAP_AND_PLAN_READY_REDUCERS_NOT_STARTED`다. Reducers는 아직 시작하지 않았다.

| 항목 | 결과 |
|---|---:|
| Source records | 823,279 |
| Semantic units/capsules | 52,644 / 52,644 |
| Map checkpoint requests | 7,513 |
| Compatible map checkpoint hits | 7,498 |
| Fresh map outputs | 15 (모두 `gpt-6.1-sol/high`) |
| Capsule exact matches / changed-or-new | 52,575 / 69 |
| Reduce leaf nodes | 4,969 |
| Fixed model tasks | 1,868 (1,858 reducers + 9 category review + 1 world root) |

Reuse model provenance: 5.6/xhigh 7,462 outputs, 6.1/high 36 outputs; 15 fresh outputs는 전부 6.1/high.
Capsule population root는 `82aa7d0d29067af25f9fe8f410bae6c64e8cea00b724571d810fc46018f0d7e0`.
Final plan SHA-256은 `6a3c78d89c55233afd66ef556eeb4cfba88c51047e140cccc268f95d5d51fc97`, topology SHA-256은
`0663df89a0805c91f8526fc8a5126da004b06a20bbf0b7ac8ecb1978daa78ed5`. Map usage ledger
`map_stage_checkpoint_usage.jsonl` SHA-256은 `36a92c903b7315e78ce4c36f676a6d98a69ba9ac31a06fc8e53af6b5b6282f3b`.
과거 1,867-task preflight는 map 결과 전 provisional이므로 현재 분모가 아니다.

Continuation 검증에서 기존 receipt에 `resume_source_database_wal_sha256` property가 빠진 것을 발견했다.
compiler `offline_v2.py`에 metadata 값을 receipt에 기록하는 수정과 regression test가 있었고, 그 뒤
focused regression과 전체 gates가 통과했다: Ruff PASS, Mypy 139 source files PASS,
pytest 1,925 passed / 1,244 warnings (308.49 seconds).

2026-10-05 read-only verification에서 target DuckDB `offline_compile_metadata`의 resume main/WAL SHA가
source file hash와 attested 값에 일치했다. Corrected receipt는 structured JSON으로 해당 WAL property만
추가했고, 원본 bytes는 `offline_map_plan_receipt.json.pre_wal_attestation`에 보존했다.

| Artifact | SHA-256 |
|---|---|
| Original receipt backup | `cc92a46f02c4f4dbda2b428882b0257c438d615558998f15c0e3c4e777d2fd8e` |
| Corrected map receipt | `cdb18ac16d5806cff9f440edac37c53de58df80dd8de966e6dcbc0a55d73918c` |
| Reduce DAG plan file | `c061217a7defbc19833f1e8e00c858c1028ba4f42d3b6685cfc01c1433122040` |
| Map usage ledger | `36a92c903b7315e78ce4c36f676a6d98a69ba9ac31a06fc8e53af6b5b6282f3b` |

Receipt fields other than the WAL SHA, plan/topology hashes, capsule population root, and the 7,513-row usage
ledger are unchanged. No map rerun was performed. The next operation is the same build's
`--continue-after-map-plan` reducer continuation, after rechecking these identities and current process state.

## 2026-10-05 continuation blocker: duplicate capsules

The first `--continue-after-map-plan` attempt exited in 7.6 seconds, before reducer execution, with:

```text
resume work database capsule rows are duplicated or exceed the unit set
```

Read-only inspection found 52,713 target capsule rows for 52,644 semantic units. Exactly 69 units had two
rows. A read-only comparison with the attested resume database proved each pair had one exact old source row
(including capsule ID, payload, category, availability, and embedding) and one capsule ID absent from the
resume source. The count of ambiguous pairs was zero; the map receipt independently records 69 changed/new
capsules. No reducer or model call was reached by the failed continuation.

Root cause was `_write_capsules_to_database()` using `INSERT OR REPLACE` on `capsule_id`, while the table's
primary key is capsule ID and a regenerated capsule can have a new ID for the same semantic unit. The old row
was therefore not replaced. The implementation now rejects duplicate semantic-unit inputs and transactionally
deletes existing rows for the incoming semantic units before inserting their current capsules. It verifies the
persisted result has exactly one capsule row per incoming unit. The unit regression now changes a capsule ID
while keeping the semantic unit fixed; the targeted map-plan continuation regression also passed.

The initial broad post-delete audit query hit DuckDB's 1 GiB cap:

```text
_duckdb.OutOfMemoryException: could not allocate block of size 256.0 KiB (953.5 MiB/953.6 MiB used)
```

The transaction rolled back. A read-only check confirmed the DB remained at 52,713 rows / 52,644 units / 69
duplicates. A second redundant full-baseline join failed before any transaction with a 16 MiB allocation
error at the same memory cap. No source, target capsule row, receipt, or DAG plan changed in either attempt.
The final repair used the preverified 69-ID exact-match list and narrow key-filtered queries, not a broad
embedding join.

In one transaction, it removed only the 69 target rows that exactly match the unchanged resume source and
kept all 69 new map capsules. Post-commit checks found 52,644 target rows / 52,644 distinct units, zero
duplicate semantic units, all new capsule IDs retained, and every stale old ID absent. The repair ledger is:

```text
brain/.work/OFFLINE-COMPILE-0dd9198ac9ef79215ab1/semantic_capsule_duplicate_repair_20261005.json
```

The pre-fix receipt and first map usage ledger are also preserved beside the target DB:

```text
offline_map_plan_receipt.json.pre_wal_attestation
offline_map_plan_receipt.corrected_map_only_20261005.json
map_stage_checkpoint_usage.first_map_only_20261005.jsonl
```

Latest code gates after the capsule-persistence fix: Ruff PASS, Mypy 139 source files PASS, full pytest
1,925 passed / 1,244 warnings in 318.00 seconds. Map-only outputs, plan, receipt, usage ledger and hashes
were not regenerated or changed by the target DB repair. The status below supersedes the initial report's
"no reducer has started" statement.

## 2026-10-05 reducer continuation status

The active goal was resumed only after a read-only preflight. No `build-offline` process was present before
launch. The compiler worktree remained on `codex/v5-gpt61-high-offline` at HEAD
`b150a77c8967bc15584d117cc1d48c0b5ca78063` with the existing 10 modified source/test files preserved.
Codex OAuth reported `Logged in using ChatGPT`. The same compile was started with `--continue-after-map-plan`,
`gpt-6.1-sol/high`, concurrency 4, the attested source manifest, and compatible cache identity
`gpt-5.6-sol/xhigh`. The successful map-only stage was not rerun.

Preflight rechecked the corrected map receipt SHA-256
`cdb18ac16d5806cff9f440edac37c53de58df80dd8de966e6dcbc0a55d73918c`, plan artifact SHA-256
`c061217a7defbc19833f1e8e00c858c1028ba4f42d3b6685cfc01c1433122040`, plan SHA-256
`6a3c78d89c55233afd66ef556eeb4cfba88c51047e140cccc268f95d5d51fc97`, topology SHA-256
`0663df89a0805c91f8526fc8a5126da004b06a20bbf0b7ac8ecb1978daa78ed5`, and map usage ledger SHA-256
`36a92c903b7315e78ce4c36f676a6d98a69ba9ac31a06fc8e53af6b5b6282f3b`. Resume-source main/WAL file hashes
matched the hashes in the target DB metadata and receipt. Read-only DuckDB counts were 823,279 assignments
and 52,644 capsules for 52,644 distinct semantic units; duplicate capsule units were zero, and reducer nodes
and claims were zero before continuation.

The running Python process was PID `55944`; its executor session was `31897`. At 2026-10-05 01:43:29 KST,
the target `progress.json` recorded phase `offline_reduce`, 27 of 1,868 model-task nodes completed, and current
node `REDUCE-ee59911f6d792006f332`. This node count is DAG closure progress, not the number of fresh GPT
requests. Provider checkpoint reuse/fresh/failure totals have not yet been sealed and must not be inferred
from the node count. At the same observation Python private memory was about 3.49 GiB, available RAM about
24.4 GiB, and C: free space about 248.4 GiB. The process was live and progress had advanced; the run was not
restarted.

If this execution is interrupted, first check whether PID/session is still active; never start a duplicate
build while it is live. If terminal, inspect the exit status and persisted target nodes, revalidate the same
receipt/plan/usage/source identities and capsule closure, then resume the same compile. Do not rerun map-only.
Reducer/category/world synthesis, immutable package and audit, daily integration, formal A/B/C evaluation,
pre-open shadow, release/rollback, and Korean commit/push remain incomplete.

## 2026-10-05 reducer failure and narrow citation recovery

The continuation above did not remain active. It terminated with exit code 1 at 2026-10-05 01:44:26 KST.
The exact exception was:

```text
semantic reduce claim cited an unavailable capsule
```

The last persisted `progress.json` remains at `offline_reduce`, 27/1,868 model-task nodes, updated
`2026-10-05T01:43:29.789174+09:00`, current node `REDUCE-ee59911f6d792006f332`. The 27 persisted
reduce nodes are retained; read-only inspection found zero `mechanism_claims` and zero `claim_capsules`.
The target DuckDB grew to approximately 5.42 GB during this attempt. No active Python `build-offline` process
was found on the next process check. Do not infer that the stale progress timestamp means the job is still live.

The failing fresh request has trace `TRACE-884c55c36a5f.json`, purpose
`offline_semantic_reduce.REDUCE-f67a3f4f68d50b990958`, status `ok`, and checkpoint
`LLMCKPT-70c68d0c46a145a5`. The checkpoint SHA-256 is
`5859534D15ED921E923EE4D070C8D9F1369C3D12877F91C8E43B290D29B0018B`. It had six leaf children, four claims,
and twelve citation values. Exactly one value was malformed:

```text
CAP-13411f263ef2c1cde6ae ج
```

The prefix `CAP-13411f263ef2c1cde6ae` was an exact member of that node's 24 allowed evidence IDs; only one
trailing Arabic letter was extra. The trace/checkpoint were successful at the provider/schema layer, but local
dynamic citation-membership validation rejected the output after the checkpoint had already been written. A
blind resume before correction would hit the same cached output and fail again. Preserve the original trace and
checkpoint unchanged.

In the existing dirty compiler worktree, a narrow deterministic normalizer was added to
`src/news_scalping_lab/brain/offline_v2.py`, backed by an audit contract in
`src/news_scalping_lab/contracts/offline_brain.py` and regression coverage in
`tests/unit/test_offline_brain_v2.py`. It accepts an exact allowed ID unchanged, or removes exactly one
non-ASCII alphabetic suffix only when the preceding full prefix is itself an exact allowed ID. It records the
original value, normalized ID, claim/citation position, and rule in `citation_normalizations`, which is part of
the persisted reduce-node payload. Unsupported IDs, ASCII suffixes, and other malformed forms remain
fail-closed. No prompt, source data, map output, plan/DAG identity, or checkpoint was edited, and this fix adds
no model request.

Verification after this change so far:

- Focused Ruff check over the three changed files: PASS.
- Focused regression command covering unsupported IDs, one permitted Unicode-letter suffix, and omitted child
  nodes: 4 passed.
- Full repository Ruff, full Mypy, and full pytest after this new change: NOT RUN; required before continuation.

Next action is not another immediate build. First confirm the actual process is still absent and target/receipt/
source identities and 27 persisted nodes still agree; then run the full three quality gates in the compiler
worktree. If they pass, continue this same compile with the existing `--continue-after-map-plan` identity,
`gpt-6.1-sol/high`, concurrency 4, and preserved checkpoint/DB/WAL. Do not rerun import, embedding, or map-only.
If a gate fails, fix narrowly, retain all dirty work, and rerun the required gates before starting the provider.

## 2026-10-05 full gates passed; continuation authorized

The active `build-offline` process check returned no Python CLI process. The compiler worktree is still branch
`codex/v5-gpt61-high-offline`, HEAD `b150a77c8967bc15584d117cc1d48c0b5ca78063`, with the same 10 dirty
tracked files preserved. The target status remains `offline_reduce`, 27/1,868; target DB has 5,424,295,936
bytes, SHA-256
`5a22a6cbfde180e2233cd7599575c12dd956f14afc7d7a90c2be9ea4cc498573`, and no WAL sidecar. Read-only DuckDB
queries reconfirmed 823,279 assignments, 52,644 primary units, 52,644 capsules, 52,644 centroids, missing
primary capsules 0, duplicate units 0, 27 reducer nodes, and 0 claims.

Hashes were rechecked before any new model call:

```text
resume-source main SHA-256  0aecf3a7a22a17927c7feec3cf46a07bbdc5e67d804eab21e11cee27f40de7d6
resume-source WAL SHA-256   dc9507249bb32c52290a5e854706ce01d51783edd8466d568874ed7218c745e2
map receipt SHA-256         cdb18ac16d5806cff9f440edac37c53de58df80dd8de966e6dcbc0a55d73918c
reduce plan artifact SHA    c061217a7defbc19833f1e8e00c858c1028ba4f42d3b6685cfc01c1433122040
reduce plan logical SHA     6a3c78d89c55233afd66ef556eeb4cfba88c51047e140cccc268f95d5d51fc97
topology SHA-256            0663df89a0805c91f8526fc8a5126da004b06a20bbf0b7ac8ecb1978daa78ed5
map usage ledger SHA-256    36a92c903b7315e78ce4c36f676a6d98a69ba9ac31a06fc8e53af6b5b6282f3b
```

The source snapshot manifest file still hashes to the explicitly attested
`6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`. The legacy retrieval `current.json`
pointer reports a different pointer hash `fc0d847d4eb0db688cf19570a3519d357a93558463797e26321a106d60040804`;
it was not edited, and the CLI invocation supplies the attested actual manifest SHA as required.

Full gates in the compiler worktree after citation normalization:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,927 passed, 1,262 warnings, 296.02 seconds
```

Codex OAuth status was `Logged in using ChatGPT`. `brain build-offline --help` confirms
`--continue-after-map-plan` and the existing checkpoint options. Its command help also explicitly says this
build is **evaluation-only** and output includes `production_activated: false`; do not present it as a
production-selected brain. The full daily architecture, blind evaluation, and separate production-release binding
still need verification before any activation.

At this evidence point the next action is the same `OFFLINE-COMPILE-0dd9198ac9ef79215ab1` continuation with
`gpt-6.1-sol/high`, concurrency 4, and the preserved DB/checkpoints. Do not rerun import, embedding, map-only,
or the persisted 27 nodes. Record the actual process/session ID and first updated ledger before reporting it as
active.

## 2026-10-05 same-compile continuation live snapshot (superseded below)

After all three full code gates passed, the continuation was restarted with the identical source manifest, corrected
map receipt, target compile ID, checkpoint directory, compatible `gpt-5.6-sol/xhigh` cache identity, and new
offline request identity `gpt-6.1-sol/high`, concurrency 4. No import, embedding or new map plan was run. The
build process is Python PID `56480`; executor session `13001` is live.

Progress first moved through `representative_and_distribution_resume`, then to `offline_reduce`. The reducer
response that previously failed citation membership was a matching `checkpoint_hit`; the exact allowed capsule
prefix plus one Unicode-letter suffix was normalized and audited by the new code, and one additional reduce node
was persisted. At 2026-10-05 02:38:42 KST, progress was 28/1,868, current node `REDUCE-f5475534d683414f88ae`.
This is one newly closed DAG node, not one fresh model call. Read-only DB counts were 28 reduce nodes and zero
mechanism claims at that observation.

The compiler worktree `runs/traces` contained exactly 7,514 traces modified since this continuation began: 7,423
`offline_semantic_leaf`, 90 `offline_long_payload_map`, and 1 `offline_semantic_reduce`. Every one reported
`checkpoint_hit`; no fresh provider output was observed in that batch. These are checkpoint reuse events, not
7,514 new GPT calls. Continue aggregating later traces separately by status/purpose/model; do not count a cached
response as a new request.

Resource sample during the local resume phase: working set about 2.96 GB, private bytes about 3.74 GB, available
RAM about 22.8 GiB, C: free about 246.4 GiB. Target DuckDB grew through resume/index preparation and was about
4.75 GB at the latest file observation; successful node persistence was retained. The target file is mutable
while this process is live, so do not hash or open a competing writer against it. Watch heartbeat, progress,
memory and free space; do not start another build while PID/session remain live.

## 2026-10-05 second continuation failure: empty child identity

The second same-compile continuation terminated with exit code 1 at 2026-10-05 02:39:30 KST. The exact error
was `semantic reduce output omitted or added children`. No Python `build-offline` process remained after the
session ended. The last ledger stayed at `offline_reduce`, 28/1,868, current ID
`REDUCE-f5475534d683414f88ae`, updated `2026-10-05T02:38:42.869272+09:00`. Read-only DuckDB confirmed 28
persisted reduce nodes, zero mechanism claims, and the failing node was not stored. Preserve the 28 nodes.
After the failed process closed, the target main database SHA-256 was
`8DC3C28C01716D758BA009C396F5B4EB7A43F0F990E535B2C3C2BCFCD4461F5A`; no target WAL sidecar existed. A fresh
pre-resume read-only audit reconfirmed 823,279 assignments/unique records, 52,644 primary units/capsules/
centroids, missing primary capsules 0, duplicate units 0, 28 reduce nodes, and zero claims. Compile metadata's
source main/WAL, source manifest, plan and topology identities matched the sealed receipt.

The rejected fresh `gpt-6.1-sol/high` trace is `TRACE-58c3c25b24bd.json`, purpose
`offline_semantic_reduce.REDUCE-1f4f08f2a70b89b8b539`, status `ok`, input SHA-256
`717489e88e789f2342ab278853962c47cb6977d990c805ff1a9276124ad0ff7e`, output SHA-256
`0f803b6dc6dfb2ebe30180adee00348cf24ad154da0946582145375db8672a17`, and trace file SHA-256
`F9F727AA2ED2EB8DB34591BF5B34C3FC866906F3A5E03DEDA4FCC39AE41AD943`. The checkpoint is
`LLMCKPT-2555ed258597d1fe.json`, file SHA-256
`9685BB64127B76E9FE0D6513F456CB5A407F20EF7F0B3FAAB4D5769AA3979C88`. The model's `node_id` matched but
`child_node_ids` was `[]`; the sealed plan has exactly 10 children for this node. No part of that checkpoint or
trace was edited.

The continuation's 7,515 touched traces were aggregated: 7,423 leaf-map hits, 90 long-payload map hits, and one
previous reducer checkpoint hit; all 7,514 reported `checkpoint_hit`. The single `ok` trace above was one fresh
provider output, rejected locally before node persistence. The 7,513 map payloads were reused and not regenerated.

In the existing dirty compiler worktree, empty child identity now normalizes only when the returned node ID is
exact and the returned child list is exactly empty while the locally planned child list is non-empty. The compiler
restores the sealed local list and persists `child_identity_normalization` with the original empty list, restored
IDs, and rule. A wrong node ID, partial list, or nonmatching child IDs still fails closed. Citation membership,
coverage count/root, and downstream graph checks remain based on the local fixed graph. Prompt/DAG/checkpoint
identity did not change. New unit tests cover both category reducer and world reducer; the category test round-trips
the audit metadata through DuckDB persistence/load. The existing partial-child-omission rejection still passes.

Full gates after this second correction:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,929 passed, 1,280 warnings, 295.36 seconds
```

The next step is to recheck there is no active build, verify source/target/receipt/plan/usage identities and the 28
saved nodes read-only, then resume the same compile with `--continue-after-map-plan`. Keep both rejected output
checkpoints. Do not rerun import, embedding, map-only, or any valid persisted reducer nodes.

## 2026-10-05 third same-compile continuation: live status

The third continuation is for the existing `OFFLINE-COMPILE-0dd9198ac9ef79215ab1`, with the existing source
manifest, checkpoint directory, `--continue-after-map-plan`, request model `gpt-6.1-sol/high`, concurrency 4,
and compatible checkpoint identity `gpt-5.6-sol/xhigh`. It did not rerun import, embedding, map-only, or planning.
The invocation started at 2026-10-05 02:59:37 KST. At 03:05 KST it remained active as Python PID `37500`,
executor session `57997`.

The progress ledger at 03:00:18 KST showed `representative_and_distribution_resume` and
`processed_record_count=823279/823279`, `record_progress_ratio=1.0`. That field is the source-record resume
counter, not synthesis completion. At 03:05:38 KST, the same ledger had advanced to `offline_reduce`, with
32/1,868 model-task nodes closed and current ID `REDUCE-dbf362b7bb2ff00c0eff`; the fixed-DAG closure remainder
was 1,836 nodes. This does not mean 32 fresh model calls. The fresh-output versus checkpoint-hit trace ledger has
not yet been classified for this third run. Filesystem observation found 7,517 trace JSON files created or
modified since process start, but that count is not a provider-call count.

At 03:05:44 KST, PID `37500` was still present; working set was about 3.12 GiB and private bytes about 3.83 GiB.
This is one resource sample, not evidence of a leak or an ETA. While the build is live, do not start another build,
hash/open the mutable target DuckDB, or run a competing writer. Observe progress/heartbeat/resources only. After
termination, capture the executor exit result, verify target/receipt/plan/source identities and actual persisted
nodes/claims read-only, classify fresh versus reused traces, and continue the same compile only if integrity holds.

The current goal document was synchronized with this observation. Its Downloads mirror is
`C:\Users\eorb9\Downloads\codex_goal_nslab_finish_brain_and_daily_csv.md`; verify its SHA-256 against the repo
copy after every document update.

### 03:07 KST progress refresh

At 2026-10-05 03:07:08 KST, `progress.json` advanced to 36/1,868 model-task nodes, phase `offline_reduce`,
current ID `REDUCE-16f7ed9c7f98355bd78a`; fixed-DAG remainder 1,832. This is four additional ledger-closed nodes
since the 03:05:38 observation, not four proven fresh GPT calls. At 03:07:29 KST, PID `37500` was still active.
Working set was about 3.12 GiB and private bytes about 3.83 GiB. No target database read/write audit was run while
the compiler held the live database. The Downloads goal mirror was recopied from the repo document and SHA-256
verified equal at `ED09335DE75EBE738A01255B97E904747BD20723DBD090855B664A4E4CE376A4`.

### 03:08 KST progress refresh

At 2026-10-05 03:07:58 KST, `progress.json` advanced to 37/1,868 nodes, phase `offline_reduce`, current ID
`REDUCE-1a405e00c704ee7a0873`; fixed-DAG remainder 1,831. At 03:08:28 KST, PID `37500` remained active with
working set about 3.12 GiB and private bytes about 3.83 GiB. Trace fresh/reuse classification and target database
read-only closure audit remain pending until the process terminates.

At 2026-10-05 03:08:55 KST, the ledger advanced again to `offline_reduce`, 39/1,868 nodes, current ID
`REDUCE-17f8bf1f630040100946`; fixed-DAG remainder 1,829. This is two more ledger-closed nodes since the 03:07:58
sample, not two proven fresh GPT calls. The latest process check still found PID `37500`; no competing database
audit was run.

At 2026-10-05 03:11:26 KST, the ledger showed 46/1,868 nodes, phase `offline_reduce`, current ID
`REDUCE-da81616deed51b6a150f`; fixed-DAG remainder 1,822. The Python build process PID `37500` was still present
at 03:11:34 KST. This progress is DAG closure, not the number of fresh calls. The target DB remains untouched by
an external reader/writer while the run is active.

### 03:24 KST progress and trace refresh

At 2026-10-05 03:24:02 KST, `progress.json` reported phase `offline_reduce`, 71/1,868 model-task nodes,
current ID `REDUCE-8ec592696eb8620ddaef`; fixed-DAG remainder 1,797. Python PID `37500` remained active at
03:24:06 KST. Process memory was about 3.13 GiB working set and 3.83 GiB private bytes.

Reclassification of all trace JSON files modified since this continuation began found:

```text
trace rows                    7,555
checkpoint_hit                7,514 (7,423 semantic_leaf; 90 long_payload_map; 1 reducer)
fresh successful reducers        41
failed or other statuses           0
fresh prompt token estimate  5,280,083
fresh completion estimate       80,748
```

Fresh successful trace start times span 03:03:36-03:21:10 KST, about 17m34s, for an observed average of
about 2.33 fresh outputs/minute. A straight-line calculation for the 1,797 remaining closure nodes is about
12h51m, but this is one short sample with variable prompt sizes and provider wait gaps, not a dependable ETA.
The runtime remained active; continue observing the same process and remeasure a later steady interval. Token
counts are trace estimates, not billing totals.

## 2026-10-05 03:19 KST trace and downstream path audit

At 03:17:56 KST, the live progress ledger reported `offline_reduce`, 61/1,868 nodes, current ID
`REDUCE-7510f8b25206c663f007`; fixed-DAG remainder 1,807. PID `37500` was still live at 03:18:56 KST.

Trace JSON files modified since the 02:59:37 start were classified by their recorded status and purpose:

```text
total trace rows             7,546
checkpoint_hit               7,514
  offline_semantic_leaf      7,423
  offline_long_payload_map      90
  offline_semantic_reduce        1
fresh status=ok reducers        32
failed / other status             0
```

Successful fresh traces estimate 4,117,842 prompt tokens and 61,205 completion tokens. Those are provider trace
estimates, not billing totals. Fresh reducer successes occurred from 03:13:26 to 03:16:16 KST. That short burst
is not a stable basis for total ETA. The process was still active after the last fresh trace; do not infer it is
stalled solely from the gap. Continue polling the same PID/session and progress ledger.

Read-only code audit for downstream gates found:

- `ThinDailyAnalyzer.analyze` loads `BrainPackageDailyContextProvider` with retrieval basis `CURRENT_NEWS` before
  the sole `final_market_decision` call; the daily run manifest records one logical call, at most one repair, zero
  daily imports/rebuilds, zero BLIND web calls, zero online corpus scans and future records. Existing unit tests
  assert the one-call behavior, open-world brain prompt, call-count independence from 823,279 records, and no LLM
  calls inside loops. This is code/test evidence only; a real built-package CSV smoke remains required.
- `BrainPackageDailyContextProvider` validates package root, ANN readiness and actual HNSW query plans, then
  performs bounded read-only index retrieval from current-news embeddings. Runtime package/cutoff/citation closure
  remains untested until the immutable build exists.
- `score_thin_daily_quality` reports
  `HOLD_FOR_REGISTERED_QUALITY_GATES_AND_EXTERNAL_REVIEW`; repository search found no separate registered numeric
  threshold registry for this new A/B/C architecture. Do not invent thresholds or promote based on smoke.
- Current production `release.py` artifact projection references only the legacy `brain/current/brain_manifest.json`,
  memory manifest, shadow manifest and doctor report; its release manifest records a legacy brain version, not the
  V2 package root/pointer. Therefore final signed release binding of the audited V2 package is an explicit G7 gap
  to solve and test before activation. Production activation remains HOLD meanwhile.

## 2026-10-05 05:02 KST reducer citation recovery and same-compile resume

### Stale request and failure evidence

At 04:27 KST the full-corpus build was still present as Python PID `37500`, but the progress ledger had not changed
since 04:00:24 KST: `127/1,868`, phase `offline_reduce`, current node `REDUCE-148b5b437d27918792a8`. The console
had reported `semantic reduce claim cited an unavailable capsule` and a 300-second executor thread-join warning.
At 04:39 KST its Codex leaf had been alive since 04:00:44, with no `last-message.txt`, no new trace since 04:01:48,
unchanged process I/O counters over a 16-second sample, and no observable CPU progress beyond about 1.25 seconds.
The two HTTPS sockets remained established, so elapsed time alone was not used as the stop reason; the combination
of stale ledger, absent output, frozen I/O, and the failed reducer established a hung request after the executor
error.

The rejected response is retained unchanged as checkpoint `LLMCKPT-25cbe98626525c18`, purpose
`offline_semantic_reduce.REDUCE-fc1dd62145fd18208293`, in trace `TRACE-adc6e7fc6eb6.json`.

```text
trace SHA-256      D4BF7B75C4F17A721C2A8B6B64C2C8AFC4BB30FEB8161069229C83FFD5C1413A
checkpoint SHA-256 39FEA81B5058E015A98465B8074AE8C3C6871CCE71198C017B8D60563337B368
```

The malformed value was `CAP-69a102da42987718cbbd` followed by Unicode U+00AD SOFT HYPHEN and U+2014 EM DASH.
The fixed plan assigns that reducer these two leaf children:

```text
LEAF-BUCKET-519e40498b2975e22884
LEAF-BUCKET-5e187523e9a4bf1cefa5
```

After the build process ended, the target database was opened read-only. Reconstructing the exact runtime leaf
algorithm from its persisted `market_memory` capsules confirmed that the union of those two children's allowed
evidence IDs contains `CAP-69a102da42987718cbbd`. The normalization therefore removes only the observed exact
two-codepoint suffix when its preceding full ID is a member of the current node's allowed set. The original value,
normalized ID, claim/citation position, and normalization rule are persisted. Other suffixes and unknown IDs remain
fail-closed.

No source, input, prompt, plan/DAG, checkpoint, receipt, or existing reducer row was rewritten. The correction is
limited to local citation-output normalization plus its audit enum and regression cases. The failed reducer node
was not present in DuckDB; the database had 823,279 assignments, 52,644 capsules, 127 reducer rows, zero claims,
and no WAL after shutdown.

### Verification and resume

The same fixed identities were rechecked before resume:

```text
compile ID                  OFFLINE-COMPILE-0dd9198ac9ef79215ab1
source manifest SHA-256     6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576
record corpus root          2d25581cdc98d89cb0f1d2fa00bec917442171ee279c001edfc764e2941f6d75
logical reducer plan SHA     6a3c78d89c55233afd66ef556eeb4cfba88c51047e140cccc268f95d5d51fc97
reducer topology SHA        0663df89a0805c91f8526fc8a5126da004b06a20bbf0b7ac8ecb1978daa78ed5
plan artifact SHA-256       C061217A7DEFBC19833F1E8E00C858C1028BA4F42D3B6685CFC01C1433122040
corrected map receipt SHA   CDB18AC16D5806CFF9F440EDAC37C53DE58DF80DD8DE966E6DCBC0A55D73918C
```

The actual source snapshot manifest still hashes to the attested source SHA above. Target metadata matches the
compile ID, source root/count, logical plan, topology, and resume-source database identities. There is no target
WAL. The failed request's Codex process was a child of the verified `news_scalping_lab.cli brain build-offline`
process for this project, requested `gpt-6.1-sol/high`, and was not under the protected Bithumb root. Only the stale
Codex executable leaf was stopped after saving its trace/checkpoint evidence. The build session then exited with
code 1; the target and accepted checkpoints were preserved.

Full repository gates in the compiler worktree after the correction:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,932 passed, 1,307 warnings, 295.68 seconds
```

The same compile was resumed at 04:57:55 KST as Python PID `12492`, session `38263`, with the original source,
receipt, checkpoint directory, model `gpt-6.1-sol/high`, concurrency 4, and `--continue-after-map-plan`. It did not
run import, embedding, or map provider work. At 05:01:43 KST it was back in `offline_reduce`, still `127/1,868`,
current node `REDUCE-ab54e8679978127c4919`. Since restart, 7,513 trace rows were all `checkpoint_hit` (7,423
semantic-leaf and 90 long-payload-map); no fresh provider output was counted in that replay batch. These are
cached recovery/reconstruction reads, not 7,513 new model calls. Next, observe the same process until fresh reducer
outputs advance persisted closure; do not start another build or inspect the live target database.

### 05:05 KST reducer progress after citation recovery

At 05:04:00 KST the same active compile's ledger advanced to `offline_reduce`, `132/1,868`, current node
`REDUCE-3ad5cdae097bdd66248c`; the remaining fixed-DAG closure is 1,736. This is five additional closed nodes since
the 05:01:43 observation. Traces after 05:02:00 show four fresh reducer traces with status `ok` and one
`checkpoint_hit` for the previously rejected `REDUCE-fc1dd62145fd18208293` output. The original checkpoint was
reused unchanged under the exact-suffix normalizer; no fresh model call was needed for that node. Database
normalization metadata still requires post-build read-only verification.

The resume replay batch also contains 7,513 exact map-stage cache hits (7,423 semantic leaf and 90 long-payload
map). Those are cache reads, not regenerated map output or new OAuth calls. No target DuckDB/WAL access occurred
while PID `12492` was live. Working set/private memory after the startup rehydration sample settled to roughly
3.1/3.9 GiB; C: free space was about 259 GB. These are resource observations, not leak/ETA conclusions. Continue
observing the same PID/session, keep builds serialized, and recalculate closure only from the fixed 1,868-node DAG.

### 05:41 KST continuation: release binding and build progress

The same compile remains live as Python PID `12492` with executor session `38263`; no replacement build was started.
At 05:40:12 KST its persisted progress ledger reported:

```text
phase                         offline_reduce
fixed DAG closure             199 / 1,868 (10.65%)
remaining fixed-DAG nodes      1,669
current node                   REDUCE-59ad0a8b0743005a9a01
record accounting              823,279 / 823,279 (not semantic completion)
```

Trace files completed from 05:14 through 05:37 were parsed individually from the compiler worktree's `runs/traces`.
There were 41 successful traces: 40 fresh `offline_semantic_reduce` outputs and one
`offline_category_review.continuation`; no checkpoint hits or trace errors occurred in this interval. All were
Codex OAuth `gpt-6.1-sol/high`. Their usage estimates sum to 5,363,619 prompt and 81,646 completion tokens; these
are provider-side trace estimates, not billing. The longest completed trace took 299.2 seconds. Trace count and
DAG closure are tracked separately because they are not one-to-one.

One Codex child request (`cmd.exe` PID `85176`, `codex.exe` PID `73956`, started 05:20:59 KST) remained live at
05:40, with no `last-message.txt`, about 74 MiB private memory, about 10 CPU seconds, and two established TCP
connections. Other requests and the main DAG continued to advance. This is an unresolved long-request observation,
not evidence of quota failure or a failed node. It was left untouched; recheck the same child, its output file,
traces, and ledger before deciding whether any intervention is justified.

The separate main-repo V2 release-binding changes were also revalidated. The generated
`production_release_manifest.schema.json` and `production_release_transaction.schema.json` were stale; they were
regenerated using `python -m news_scalping_lab.contracts.schemas`, and
`tests/unit/test_project_scaffold.py::test_tracked_json_schemas_match_contract_export` now passes. Current main
repo gates:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,904 passed, 1,208 warnings, 298.90 seconds
```

Focused evidence also includes 23 production-promotion tests, 16 thin-daily one-call tests, and the package-pointer
daily-reader regression. These gates apply to the present main-repo tree; rerun the full set after any compiler
integration or other code edits. The live build's DuckDB/WAL was not opened.

### 05:47 KST continuation: current reducer state

The live compile identity and executor session were rechecked. At 05:46:08 KST the progress ledger reported `207 / 1,868`
closed nodes (11.08%), `1,661` remaining, current node `REDUCE-010e846f2d66c11e6ca7`, phase `offline_reduce`.
Python PID `12492` and executor session `38263` remained live. The input accounting ratio is still 1.0 for 823,279
records and is not the synthesis ratio.

From 05:37 through 05:46, 11 newly completed traces were all fresh successful `offline_semantic_reduce` outputs;
there were zero checkpoint hits or trace errors. Estimated token totals were 1,654,193 prompt and 23,579 completion,
not billing. The longest trace was 229.3 seconds.

The long-running child from the previous note, `codex.exe` PID `73956` under project-owned `cmd.exe` PID `85176`,
was still alive at 05:46. Its `last-message.txt` was absent and two established sockets remained; the latest resource
sample showed about 74 MiB private memory and roughly 13 CPU seconds total. Other reducer traces and ledger closure
continued, so the request was not killed or declared failed. Continue observing this exact child and the same build.

The formal evaluation model identity was also verified in source: compiler builds use Codex OAuth
`gpt-6.1-sol/high`, while formal `QUALITY_FULL` daily A/B/C evaluation is currently pinned to
`gpt-5.6-sol/xhigh` by `contracts/quality_evaluation.py`. Keep these roles and model identities separate in the
final audit; changing the daily runtime profile would require an explicit profile/quality-contract change and a
new blind evaluation.

At 05:47:33 KST the live ledger advanced further to `209 / 1,868` (11.19%), `1,659` remaining, current node
`REDUCE-e351a9d33dc51732ea9a`. Python PID `12492` and session `38263` were still live at 05:48. The older Codex
child PID `73956` also remained alive without `last-message.txt`; its 05:48 sample was about 74 MiB private memory,
14 CPU seconds, and three established sockets. Preserve and recheck it; do not infer an error or success from its
age alone while the request remains connected and the overall DAG advances.

### 06:10 KST continuation: contract gates and reducer state

The main worktree's V6 capsule/influence-manifest compatibility changes now pass all required code gates:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,905 passed, 1,208 warnings, 307.84 seconds
```

The full-corpus compile was not restarted. Python PID `12492` and executor session `38263` were both observed
live at 06:10 KST. The read-only progress ledger at 06:09:35 reported phase `offline_reduce`, fixed-DAG closure
`243 / 1,868` (13.01%), `1,625` remaining, current node `REDUCE-d3eca0df4357eb8da59b`; record accounting remains
823,279 / 823,279 and is not synthesis completion.

Seventeen new traces completed between 05:59:04 and 06:09:35. All were successful fresh
`offline_semantic_reduce` outputs under Codex OAuth `gpt-6.1-sol/high`, with zero checkpoint-hit traces and zero
errors in that interval. Usage estimates total 2,351,597 prompt and 35,789 completion tokens; these are trace
estimates, not billing. Trace rows and DAG node closure are reported separately.

The long Codex child is still unresolved, not declared failed: process ancestry was verified as compile PID `12492`
-> `cmd.exe` PID `85176` -> `node.exe` PID `42708` -> `codex.exe` PID `73956`. At 06:09:39 its output
`last-message.txt` was absent, private memory was about 74 MiB, cumulative CPU about 25.6 seconds, and two HTTPS
connections were established. Other reducer traces and closure continued to advance, so it was left untouched.
Recheck this exact request before any intervention.

The live build's target DuckDB/WAL was not opened. Available RAM was about 20.8 GiB and C: free space about 239.7
GiB during the pytest run. The post-patch full test run completed without stopping or duplicating the compiler.

### 06:22 KST continuation: rollback proof and final code gates

The main-repo release implementation had a rollback command but no direct regression test for returning to a prior
signed release. Added `test_rollback_reactivates_a_verified_previous_release`, which creates two temporary release
fixtures, activates A then B, rolls back to A, and verifies that the current pointer is valid and records B as its
previous release. The focused rollback test passed. No real production release or pointer was activated or changed.

After this last code change, the final repository gates passed:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,906 passed, 1,208 warnings, 300.72 seconds
```

The full-corpus compile remains the same active `OFFLINE-COMPILE-0dd9198ac9ef79215ab1` process, not a duplicate.
At 06:21:36 KST its ledger reported `263 / 1,868` closed nodes (14.08%), `1,605` remaining, current reducer
`REDUCE-cc970084d4f760563db6`. Python PID `12492` was live at 06:21:40 and executor session `38263` replied to a
poll at 06:22. Source accounting remains 823,279/823,279, not synthesis completion.

From 06:09:35 to 06:21:40, 21 new traces were all successful `offline_semantic_reduce` outputs for
Codex OAuth `gpt-6.1-sol/high`; no checkpoint-hit trace or trace error appeared in this interval. Estimated usage
was 2,868,691 prompt and 47,597 completion tokens, not billing. Trace count is not DAG closure.

At 06:21:40 the long child ancestry remained `12492 -> 85176 -> 42708 -> 73956`. Its `last-message.txt` was still
absent; private memory was about 74 MiB, CPU about 32.6 seconds, and no established TCP connection was observed in
that sample. The main DAG and other reducer traces were advancing, so this child was left running. Recheck it before
any intervention. The target DuckDB/WAL was not opened or hashed.

The tested CLI rollback syntax, if a production owner later authorizes a rollback, is
`python -m news_scalping_lab.cli production rollback --release-id <verified-previous-release-id>`. The promotion
HMAC key must come through the existing settings/environment path and must never be passed as an argument or
committed. The CLI was not executed against production.

### 06:25–07:07 KST: failed reducer citation, bounded repair, same-compile resume

The first resumed full-corpus attempt stopped with exit code 1 near 06:25 KST. Its exact error was
`semantic reduce claim cited an unavailable capsule`. After the Python build process had exited, a read-only audit
found 52,644 persisted semantic capsules and 267 persisted reduce nodes. The audited checkpoint and trace were:

```text
trace:        TRACE-718730099afa
purpose:      offline_semantic_reduce.REDUCE-0555ca45c1a45fd5d237
checkpoint:   LLMCKPT-4a6a1cbded76a0fb
input SHA256: 702c2f18a9e9d2d8e4ede58f82e1adc99b04013b0ddaafe27a42d04cd2fd2251
output SHA256: 202c3703a8bf841718bd30ac224068889d8a4465450ee8db387fc762a89dfbd7
```

The trace and checkpoint input/output hashes matched. Reconstructing the exact allowed capsule evidence from the
four `theme_formation` leaf children isolated one malformed citation: allowed ID prefix
`CAP-f9617dd6843d2f1b6f23` followed by the Arabic token `عند` (U+0639 U+0646 U+062F). The prefix was in the
node's allowed set; only the appended suffix caused exact membership validation to fail. This was not a missing
source record, child mismatch, or a reason to relax citation membership generally.

Compiler commit `8578372` adds `_normalize_reduce_claim_citations` with one narrow rule,
`allowed_capsule_id_plus_exact_arabic_word_suffix`: normalize only this exact ID-plus-token form and only when
the ID prefix belongs to that node's reconstructed allowed set. It persists a `SemanticReduceCitationNormalization`
audit row containing the original value and rule. Unknown IDs and arbitrary suffixes remain rejected. Focused
regression coverage verifies both the allowed exact suffix and rejection of a suffix on an unallowed ID. The
compiler worktree then passed:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,934 passed, 1,325 warnings
```

The fix was committed in Korean and pushed to `origin/codex/v5-gpt61-high-offline`. The compile resumed without
changing its source manifest, record root, compile ID, target database, fixed plan/topology, or checkpoint
directory. It did not repeat import, embedding, or map. The previous target had no WAL after the failed process
exited; once the resumed build became live, its DuckDB/WAL were not opened by an independent reader or writer.

At 06:42:15 KST the resumed Python process PID `60848` started under PowerShell PID `77968` for the same
`OFFLINE-COMPILE-0dd9198ac9ef79215ab1` identity. The current process/ledger were rechecked at 07:07 KST: Python
PID `60848` remained live, and the ledger at 07:07:39 reported `317 / 1,868` closed model nodes (16.97%),
`1,551` remaining, phase `offline_reduce`, current node `REDUCE-7aa05be27e83a8327af3`. Record accounting remains
823,279/823,279 and is not semantic synthesis completion. The 1,868-node denominator remains 1,858 reducers,
9 category reviews, and one world root. At the same sample the compiler process used about 4.11 GB private
memory; earlier samples showed four Codex children, matching the configured concurrency cap of four. These are
point-in-time resource observations, not proof of zero memory growth over the entire build. No ETA is inferred
from this short progress interval.

### 07:22 KST: same-compile progress checkpoint

The live Python process PID `60848` was rechecked under parent PowerShell PID `77968`; the existing executor session
also replied to a poll. The progress ledger updated at 07:22:20 KST and reported `356 / 1,868` closed model nodes
(19.06%), `1,512` remaining, phase `offline_reduce`, current node `REDUCE-58c3feaf2494aa9cadcc`. This is 39 more
closed nodes than the previous 07:07 checkpoint (`317 / 1,868`). Record accounting remains 823,279/823,279 and is
not a semantic completion metric. Private memory was about 4.11 GB at the same observation. The build remained
active; its live DuckDB/WAL was not opened. No duplicate compile or concurrent checkpoint writer was started.

### 07:31 KST: 20% fixed-DAG checkpoint

The same Python build PID `60848` remained active under PowerShell PID `77968`; the live WAL metadata advanced
with the build and was not opened. The 07:31:37 KST progress ledger reports `376 / 1,868` closed nodes (20.13%),
`1,492` remaining, phase `offline_reduce`, current node `REDUCE-8a7c9e16f7a66feacb0f`. This is 20 more closed
nodes than the 07:22 checkpoint. It does not mean 20% of records were newly read or that 20% of semantic quality
is achieved. Record accounting remains 823,279/823,279. Python private memory at the same sample was about
4.11 GB, consistent with recent samples; the build remains active and no other build was started.

### 10:02-10:35 KST: exact citation artifact and same-compile resume

The compiler session `14734` exited with code 1 at `493 / 1,868`. Post-exit checks found no writer and no DuckDB WAL;
read-only `reduce_nodes` count was 493, matching progress. The progress pointer was not treated as the failed node
because reducer work is concurrent.

The failed result was identified in checkpoint `LLMCKPT-8c7cc93bedf7c776`:

```text
node/purpose:     REDUCE-8becf902cac9db24b9ee
status:           ok provider output; validator had not accepted the node
model:            Codex OAuth gpt-6.1-sol/high
checkpoint file:  a4159dc8003e597fe508801ffcfb709e5c972048c9e4d6bb3917786ef501f5ef
input SHA256:     24fe52fd814482b1b8a3de4cdf0061d5e2a802d6242e737267e7adc9ce1a3332
output SHA256:    bcaed98cc9056d7ad2cb4ac4d3bc5089004524e524570708efaa9903807fbae8
prompt SHA256:    f2294f470dbd7974068083dc549b0a7596a1bb0d39c2790b12f088a74f31b904
```

The original claim-3 supporting citation was `CAP-e3f1c1efc61d5c29ca67 ... a`. The production leaf builder,
evidence selector, and prompt builder were rerun read-only against the 52,644 persisted capsules. The exact three
children were `LEAF-BUCKET-b44f52d4219c942b7b20` (12 capsules),
`LEAF-BUCKET-1b292e6eed64c24ce472` (11), and `LEAF-BUCKET-ece1f9d19774454d1207` (12). Their 12 allowed
evidence IDs were:

```text
CAP-bce0b9bf5b53649fb7fb  CAP-c3398eeeb79dfe6375bf
CAP-d3775cda69779754a021  CAP-d6bb4dfb81aecff8d44e
CAP-dd242ddfff0fb1b0fa0e  CAP-e3f1c1efc61d5c29ca67
CAP-f4d921030e3dcfb0002f  CAP-fc1122ffa3444a9bdba7
CAP-02f96d44c55c53b5d887  CAP-1c83edfa98363b00d74e
CAP-2313783095bd9503a237  CAP-3163c389f5c90715f150
```

The base capsule ID was allowed; only the exact trailing ` ... a` caused validation failure. Rebuilt prompt SHA,
character length (145,137), UTF-8 byte length (165,498), and canonical output SHA all matched checkpoint metadata.
The failed node had no persisted `reduce_nodes` row. This was not a missing source capsule or grounds for loosening
membership checks generally.

Compiler commit `2c05062` adds only exact ` ... a` normalization when the prefix is in that node's allowed set.
The original citation, normalized ID, and named rule are retained in audit data. Tests reject unallowed IDs,
` ... b`, ` ... ab`, and appended extra IDs. Focused tests: 19 passed. Full gates:

```text
python -m ruff check .                 PASS
python -m mypy src/news_scalping_lab  PASS, 139 source files
python -m pytest                       PASS, 1,946 passed, 1,433 warnings, 295.81 seconds
```

The Korean code commit was pushed to `origin/codex/v5-gpt61-high-offline`. The same compile/source/manifest/record
root/plan/topology/target/checkpoint identity was resumed; import, embedding, map, and planner were not repeated.
At 10:35:06 KST, session `1524` / Python PID `58140` / parent PowerShell PID `58132` reported `501 / 1,868`
closed (26.8%), `1,367` remaining, phase `offline_reduce`, pointer `REDUCE-cca8b11e69dd22324c7c`. This passes
the formerly failed node, but exact fresh-output versus checkpoint-hit totals await terminal reconciliation. A
10:35 sample showed about 3.9 GB private memory and 19.6 GB available RAM. Do not inspect the live DB/WAL.
