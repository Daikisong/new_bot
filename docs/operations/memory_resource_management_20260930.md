# Python 메모리 관리 보강 (2026-09-30)

## 범위와 정정

이번 변경은 두뇌 합성을 다시 하는 작업이 아니라 메모리 snapshot의 전수
검증에 사용하는 자원을 제한하는 작업이다. 기존 compiler v5의 prompt,
checkpoint identity, 연구자료, embedding, production pointer는 바꾸지 않는다.

앞선 replay 재시도들은 프로세스 private memory가 증가해 중단했다. 당시
실행 단계나 stack을 수집하지 않았으므로 FTS, HNSW, routing hash가 원인이라고
단정할 수 없다. 해당 가설로 추가했던 별도 worker 3개는 최종 변경에서 제거했다.
Python 객체 누수인지 native buffer 증가인지도 아직 확정되지 않았다.

코드상 replay는 임시 build 디렉터리를 만들기 전에 source snapshot과 재사용할
snapshot을 검증한다. 이 전수 검증의 DuckDB 연결에는 별도 메모리 한도가 없었다.
이것은 확인한 제한 누락이지, 이전 메모리 증가 원인을 재현해 확정한 결과는 아니다.

## 반영한 보호

- Source partition 및 streaming deep audit 연결의 DuckDB buffer 한도: 4GB.
- DuckDB 임시 디스크 한도: 16GB. 공간 부족 시 검증 실패로 처리하며 생략하지 않는다.
- 검증 SQL worker thread: 2개.
- 임시파일 위치: 해당 project의 `data/cache/memory-audit/audit-*`.
  immutable snapshot 및 전역 Windows 임시폴더에는 spill을 만들지 않는다.
- 정상 종료, 검증 오류, `KeyboardInterrupt`, 연결 설정 실패에 DB 연결을 닫고
  해당 호출이 만든 임시폴더를 정리한다. 강제 종료나 전원 장애까지 자동 정리를
  보장하지는 않는다.
- `news_scalping_lab.memory.index` INFO 로그에 manifest/hash, source projection,
  sidecar, projection comparison, cell/index 검증 단계를 남긴다. Source projection은
  10,000건마다 진행 수를 기록한다. replay snapshot CLI는 해당 로그를 stderr에
  보이도록 INFO logging을 설정한다. 원문 뉴스나 인증정보는 기록하지 않는다.

4GB는 DuckDB가 관리하는 buffer 한도다. Python heap, NumPy 배열, native extension을
포함한 전체 프로세스의 hard limit이 아니다. 후속 대용량 실행에서는 private bytes와
host available memory를 함께 관찰해야 한다. `gc.collect()` 호출만으로 해결됐다고
판정하거나 다른 프로젝트 프로세스를 종료해서 여유 메모리를 만드는 방식은 쓰지 않는다.

## 검증 근거

- 변경 관련 회귀 51개 통과: memory cells, replay snapshot, semantic upgrade split.
- 연결 설정 실패, projection 오류, 사용자 중단 시 scratch/connection 정리를 확인.
- Audit source DB가 read-only이며 검증 전후 파일 SHA가 동일함을 확인.
- 합성 데이터 150,000행으로 16MB buffer / 64MB spill 설정을 시험했다.
  DuckDB 측 관측 buffer 15,257,600 bytes, spill 3,899,392 bytes, 임시파일 4개였고
  종료 후 해당 scratch 디렉터리가 없어졌다. 실제 production 전체 메모리 측정은 아니다.
- Ruff, mypy(139개 source file), 전체 pytest **1,893 passed** (320.50초) 통과.
- `make`가 설치되어 있지 않아 동일 target인
  `python -m news_scalping_lab.cli full-check`를 직접 실행했다. Hardcoding 감사까지
  통과했지만 현재 worktree 예제 산출물의 provenance 감사에서 실패했다.
  `2026-06-24.json`의 brain SHA 불일치, `RUN-116b51828c12`의 output/trace 누락,
  accepted episode 및 company/mechanism memory의 provenance SHA 불일치가 나왔다.
  따라서 full-check 전체 통과는 아니다. 예제 산출물이나 기록된 hash는 수정하지 않았다.

전체 pytest 실행은 긴 `--basetemp` 경로에서 Windows 파일 경로 길이 오류가 발생했다.
프로젝트 캐시 아래 짧은 경로를 사용한다. Pytest가 basetemp를 비우므로 항상 새 경로를
선택하고 테스트 종료 후 이번 실행 경로만 정리한다. 시스템 전체 TEMP 청소는 하지 않는다.
`PYTEST_ADDOPTS`에 넣는 경로는 `/` 구분자를 사용해야 한다. 이번 실행에서 `\`가
제거되어 worktree 바로 아래에 잘못된 이름의 테스트 폴더가 생겼다.

검사 임시폴더 삭제는 정확한 경로를 확인했지만 실행 도구가 `blocked by policy`로
거부했다. 다른 방법으로 우회 삭제하지 않았다. 아래는 연구자료가 아닌 이번 테스트
산출물이며, Git에 추가하지 않는다. 이 정리 미완료를 실제 audit helper의 자동 정리
회귀 통과와 구분한다.

- `Userseorb9projectsnews_bot_nextdatacachepytest-memory-1d5c7b81fff04873ac78cb77bffc7db3/`
- `Userseorb9projectsnews_bot_nextdatacachepytest-memory-38f467ccd00b47d9b05edb21a92f88bd/`
- `Userseorb9projectsnews_bot_nextdatacachepytest-memory-longpath-check-20260930/`
- `datacacheptd391/`
- `data/cache/pytest-memory-first-failure-20260930/`

전체 테스트 폴더 `datacacheptd391/`는 검사 종료 후 39,445개 파일,
1,131,066,229 bytes였다. 삭제가 거부되어 남은 디스크 사용량이며 Python 프로세스
메모리 누수와는 다르다. 이후 테스트는 짧은 `/` 경로와 정리 가능한 실행 환경을
확인한 뒤 실행해야 한다.

## 다음 대용량 실행 조건

1. `PYTHONPATH`를 실제 작업 worktree의 `src`로 지정하고 import 위치를 확인한다.
   이 PC에서는 editable install이 다른 worktree를 가리킬 수 있다.
2. INFO 단계 로그와 peak private memory를 함께 수집한다. 호출 수나 처리 단계를
   모르는 상태에서 같은 장시간 replay를 반복하지 않는다.
3. 중단 시 source snapshot, checkpoint, pointer를 보존한다. 삭제 대상은 소유권을
   확인한 해당 실행의 임시 디렉터리뿐이다. Bithumb 프로세스는 건드리지 않는다.
4. 기존 BUILD snapshot의 재사용 가능성을 먼저 확인한다. Split 날짜 선택 변경만으로
   embedding이나 전체 brain을 다시 만들 필요가 있다고 추정하지 않는다.

823,279건 전체 replay 검증의 완료와 production peak memory 상한은 아직 입증하지
않았다. V5 합성은 기존 7,902회 성공 checkpoint를 보존하며 OAuth 재개 시각을 기다린다.
두뇌 package 봉인, 별도 평가 package, CALIBRATION/HOLDOUT 평가, production 활성화는
여전히 미완료다. 이 메모리 보강을 전체 goal 완료로 해석하면 안 된다.

## 2026-09-30 실제 snapshot 실행 관측

평가 전용 snapshot `MEMIDX-1f051543698019d5acc0`을 기존 parent vectors로 만들었다.
새 embedding 생성 0건, LLM 호출 0건, 연구자료 재수입 0건이다. 실행 후 Python PID가
종료했고 `data/cache/memory-audit/audit-*` scratch 디렉터리도 0개였다.

이 PC의 총 RAM은 61.6 GiB였다. Python 프로세스는 단계별로 크게 변했다. source 및
sidecar 투영에서는 대략 5-7 GiB private memory를 썼고, cell integrity 단계 종료 시
약 6.1 GiB에서 3.4 GiB로 내려갔다. retrieval-index 대조에서는 순간 high-water가
private 14.77 GiB, working set 12.37 GiB까지 올라가고 host available memory가
7.2 GiB까지 줄었다. 약 10초 뒤 private 3.93 GiB, working set 1.56 GiB,
available memory 19.3 GiB로 회복됐다. 이후 같은 단계의 private memory는 약
9.57 GiB, working set 약 7.38 GiB 부근에서 유지됐다. 이 급락/회복은 누적 누수보다
대형 검증 쿼리의 임시 할당과 회수에 부합한다. 프로세스 메모리만 보지 말고 host
available memory도 함께 봐야 한다.

중요: DuckDB `memory_limit=4GB`는 DuckDB가 관리하는 buffer 한도다. Python heap,
NumPy 및 native extension을 합친 전체 프로세스의 hard cap이 아니다. 실제 private
high-water가 14.77 GiB였으므로 4GB 설정을 전체 RAM 제한이라고 표현하면 안 된다.
`gc.collect()`만으로 DuckDB/native 작업 메모리를 회수한다고 가정하지 않는다. 이
실행에서는 단계가 끝날 때 메모리가 실제로 반환되고, audit 연결 종료 후 scratch도
정리됐다.

완료 receipt: BUILD cutoff `2026-01-01T23:59:59+09:00`, 포함 758,703건, 미래 제외
64,576건, calibration 32,474건, holdout 28,375건, 두 split overlap 0, retained
embedding 758,703건, generated embedding 0건. Manifest의 8개 artifact SHA-256을
모두 다시 대조했고 전부 일치했다. Snapshot은 `evaluation_only=true`이며
`production_ready=true` 필드가 있어도 production 활성화 승인을 뜻하지 않는다.
별도의 전체 `inspect_memory_snapshot` 재실행은 같은 대규모 deep SQL 감사를
반복하므로 하지 않았다. 따라서 여기서 확인한 것은 successful builder receipt,
실제 build/holdout overlap 검사, manifest artifact hashes, 종료 후 cleanup이다.

## Brain compiler 재개 전 메모리 감시

2026-09-30 현재 프로세스 점검에서는 Offline Semantic Brain compiler 또는
snapshot replay를 실행 중인 Python 프로세스가 없었다. 전체 Python 프로세스는
46개였지만 Codex MCP 등 상주 서비스가 포함되어 있었고, private memory 합계
1.84 GiB, working set 합계 1.06 GiB, 최대 단일 프로세스 201 MiB였다. Host
available memory는 약 19 GiB였다. 이 상주 서비스를 임의로 종료하지 않았다.
현재 실행 중인 compiler가 없으므로 지금 정리할 작업 Python 메모리도 없다.

앞의 14.77 GiB high-water는 snapshot retrieval-index 감사의 관측값이다. 약
10초 후 크게 회수된 기록은 있지만, 그 결과로 offline brain compiler의 peak나
누수 여부까지 입증된 것은 아니다. compiler는 OAuth 사용 제한 해제 후 기존
7,902개 content-addressed checkpoint에서 재개하며, 첫 재개 실행을 기준 측정으로
삼는다.

Checkpoint 경로도 worktree별로 확인했다. 기존 build root
`C:\Users\eorb9\projects\news_bot\runs\checkpoints\llm`에는 전체 7,965개
checkpoint 파일이 있고, 그중 compiler v5 metadata 파일은 7,903개다. 확인한
마지막 성공 ID는 `LLMCKPT-6fae813701eb8246`, 바로 다음 OAuth quota 오류 ID는
`LLMCKPT-1d6d8295e6996522`다. 현재 PR worktree의 기본 `runs/checkpoints/llm`은
6개 파일이며 compiler v5 metadata는 0개다. 다른 worktree의 기본 경로로 재개하면
오래된 성공 checkpoint를 못 찾아 중복 호출을 할 수 있다. `--checkpoint-dir`로 기존
폴더를 명시하도록 compiler와 두 build CLI 경로를 연결했으며, checkpoint를 복사하거나
삭제하지 않았다. 명시 경로가 없거나 directory가 아니면 LLM provider 생성 전에
실패하도록 했다.

재개 source project는 goal 명령에 적힌
`C:\Users\eorb9\projects\news_bot\production\staging\P9IMPORT-3D770A7DD72457C97098\project`다.
그 안의 snapshot manifest를 다시 해시한 값은 externally attested SHA
`6c05dcf49b301997dde3483b97f46668b5fb3f29fc2ea5fe67dc2c3e13fd4576`과 일치했고,
snapshot DB는 5,213,270,016 bytes다. source snapshot은 수정하지 않았다.

compiler 코드에서는 payload 수치와 exposure ledger를 만든 뒤 중복 projected/chunk
계획을 해제하고, leaf capsule 생성 직후 원문 prompt 행을 해제하도록 했다. Reduce는
capsule 및 검증된 child ID만 사용한다. 관련 단위 테스트 14개, Ruff, mypy 139개
source file을 통과했지만, 이 수명 단축의 실 corpus 메모리 절감량은 아직 측정하지 않았다.

실제 `python -m` import도 점검했다. `PYTHONPATH`를 지정하지 않으면 이 PC의 editable
install이 `news_bot\src`를 가져와 PR worktree의 `--checkpoint-dir` 옵션을 보지 못한다.
재개 명령은 반드시 `PYTHONPATH=C:\Users\eorb9\projects\news_bot_next\src`를 설정하고
`news_scalping_lab.cli.__file__`이 그 worktree를 가리키는지 확인해야 한다.

재개 시 verified compiler tree의 aggregate `BuildTreePrivateBytes`와
`BuildTreeWorkingSetBytes`, CPU/affinity, host available memory, pagefile을 10초
간격으로 phase와 완료 단위 수에 함께 기록한다. Root PID의 `RootPrivateBytes`와
`RootWorkingSetBytes`도 별도 진단값으로 남기되, private-memory warning/growth 기준에는
tree aggregate만 사용한다. 같은 phase의 aggregate 추세를 조사하되,
`BuildTreePrivateBytes`가 8 GiB 이상이거나 8 GiB 초과 상태에서 10초 표본이 6회 연속
증가하는 것은 경고·점검 신호일 뿐 자동 중지 사유가 아니다. 자동 중지는
host available RAM이 6 GiB 미만으로 60초 지속될 때만 적용하며, 완료 checkpoint를
보존한 뒤 receipt로 식별된 compiler tree만 중지한다. 경고가 나면 phase, working set,
host RAM, pagefile, 최근 checkpoint를 확인한다. 한 번의 순간 피크만으로 중단하거나,
반대로 `gc.collect()`만 호출하고 안전하다고 판정하지 않는다. 다른 MCP/프로젝트
프로세스는 종료하지 않는다. 최신 동작은 `offline_v5_resume_and_audit.md`와 guarded
runner에 정의되어 있으며, 이 문구가 이전의 더 넓은 중지 제안을 대체한다.

## 2026-09-30 compiler 객체 수명 보강

PR worktree의 `offline_v2.py`에는 대형 Python 객체의 불필요한 동시 보유를 줄이는
보강이 추가됐다. `_UnitBuild`는 semantic unit마다 전체 member record ID tuple을
계속 보관하지 않고 count/root만 보관한다. 전체 membership의 진실 원본은 그대로
assignment ledger이며, prompt row를 만든 뒤에는 `_UnitBuild` 목록 참조를 해제한다.
Incremental leaf 재합성도 payload plan과 변경 row를 사용 직후 해제하고, worker별
완성 capsule batch 목록을 별도 누적하지 않고 최종 unit map에 바로 넣는다.

신규 close-return status 통계는 raw record 객체 전체를 Python으로 적재하지 않는다.
DuckDB 결과를 `fetchmany(4096)` 단위로 읽어 semantic unit별 작은 count 집계만 만든다.
통계는 LLM prompt나 content-addressed checkpoint 입력에는 넣지 않고 leaf 합성 후
capsule에 붙인다. 따라서 기존 V5 prompt/checkpoint identity와 OAuth 호출 수는
바뀌지 않는다. Capsule population count와 influence root/count를 대조해 집계 누락도
실패 처리한다.

이 변경은 확인된 메모리 누수를 고쳤다는 증거가 아니라, Python heap의 live set과
중복 batch 보유를 줄이는 구조 개선이다. Python에서 참조를 해제해도 allocator 또는
native extension이 OS에 메모리를 즉시 반환한다고 보장할 수 없다. 실제 절감량과
compiler peak는 기존 checkpoint를 재사용하는 첫 재개 실행에서 PID private bytes,
working set, host available memory를 함께 계측해 판단한다. 실패해도 checkpoint와
source snapshot은 보존한다.

이번 변경 검증은 focused 58 tests, `ruff check src tests`, mypy 139 source
files를 통과했다. 전체 pytest도 짧은 basetemp `C:\ptd80471`에서 통과했다.
첫 전체 실행은 사용자 TEMP의 긴 basetemp 경로 때문에 23개 테스트가
`FileNotFoundError`로 실패했고, 짧은 경로 재실행에서 해결됐다. 실행 중 두 시점의
pytest working set은 406.1 MiB와 423.1 MiB였고 available RAM은 각각 14.48 GiB,
13.14 GiB였다. 이는 표본 관측이지 전체 실행의 peak 측정은 아니다.

pytest가 아래 두 basetemp에 각각 약 1.13 GB와 1.06 GB의 fixture 파일을 남겼다.
이것은 프로세스 메모리가 아니라 테스트 디스크 산출물이다. 두 경로만 대상으로 한
정리 시도는 실행 도구에서 정확히 `Rejected(... rejected: blocked by policy)`로
거부되어 남겨뒀다. 기존 user/generated pytest 디렉터리는 건드리지 않았다.

- `C:\ptd80471` (39,473 files; 1,131,053,654 bytes)
- `%TEMP%\nslab-full-pytest-d1f8bcb4eebb423b9fc912768d9173e6` (39,015 files; 1,059,157,354 bytes)

## 2026-09-30 bounded compiler staging follow-up

추가 확인에서 long-payload 및 leaf 합성 단계가 전체 batch 참조를 `list`와
unbounded `asyncio.Queue`에 미리 적재하고 있었다. 이는 누수로 확정된 것은 아니지만
대형 corpus에서 Python heap peak를 키울 수 있어 lazy batch iterator로 바꿨다. 각
worker는 다음 batch를 처리할 때 가져오며, 대기 batch가 전체 corpus 크기에 비례하지
않는다. 실패하면 남은 worker를 취소해 실행 중인 작업과 참조도 정리한다.

추가로 `_plan_long_payloads`는 chunking이 필요 없는 대표 row를 복사하지 않고,
chunk-map 호출 수를 전체 batch 목록 생성 없이 센다. Long-payload digest를 projected
rows에 제자리 반영하고 소비한 digest map 항목을 제거해 출력 복사본 중첩도 피한다.
회귀 테스트는 concurrency 1에서 이전 batch LLM 호출이 시작되기 전에 다음 batch를
요청하지 않는지 검증한다.

`tests/unit/test_offline_brain_v2.py` 17개 통과, `ruff check src tests`,
`mypy src/news_scalping_lab` 통과. 후속 변경을 포함한 PR CI run `36656852252`가
9m15s 만에 Ruff, Mypy, schema parity, production targeted regression, full pytest,
generated drift/whitespace를 모두 통과했다. 로컬 `ruff check .`는 기존 untracked
pytest fixture 3개에서 hardcoding lint finding 3건을 냈고 해당 generated 디렉터리는
수정하지 않았다.

현재 머신 표본에서 NSLAB/pytest Python 프로세스는 없었다. Python 42개 프로세스의
aggregate working set은 약 1.1 GiB, private memory는 약 1.6 GiB, host available RAM은
약 21.4 GiB였으며 상위 프로세스 경로는 MCP/Posting 서비스였다. 이 스냅샷은 누수
판정이 아니며 다른 프로젝트 서비스는 종료하지 않았다.

## 2026-09-30 blind selection preparation review

별도 프로세스 점검에서는 Python 프로세스 50개 중 NSLAB build/evaluation 프로세스는
0개였고, host available memory는 약 19.0 GiB였다. MCP 및 Posting 서비스는 다른
프로젝트 소유이므로 종료하지 않았다. 이 수치는 누수 발생 여부가 아니라 한 시점의
관측값이다.

평가 프로젝트의 공식 split source ledger 파일 크기는 CALIBRATION 40개 합계
113.03 MiB(최대 7.93 MiB), HOLDOUT 40개 합계 155.36 MiB(최대 12.07 MiB)였다.
기존 `FULL_SPLIT` 준비기는 검증된 JSONL의 파싱 결과를 split 전체에 걸쳐 보유하므로
파일 bytes보다 Python heap peak가 커질 수 있었다. 변경 후 `FULL_SPLIT`은 한 사례의
JSONL을 읽고 그 사례를 바로 봉인한 뒤 다음 사례로 이동한다. 각 ledger는 해시 검증과
사용을 같은 읽기 결과에 대해 수행해 TOCTOU 회귀 테스트의 단일 읽기 조건을 유지한다.
`THREE_CASE` 진단은 min/median/max 사례를 고르기 위해 여전히 후보 행들을 함께 보유한다.

blind source derivation은 공식 parent selection SHA
`46cd4af66271910e837b6c6bf4681d2f1980f3ce79f6466d09dc2796a3d0ba81`, plan SHA
`7ca4f1ad4471759fec09b66bfe5640ef997abd67a467aa58e091aeec41f7c63e`에서
CALIBRATION/HOLDOUT 각각 40건을 파생했다. 각 파생 manifest에서 40개의
`outcome_ledger` 참조를 제거했으며 outcome 파일을 해석하거나 열지 않았다. 결과는
`runs/semantic_brain_upgrade/shadow_split/` 아래에 저장되었다.

첫 blind input 준비는 프로젝트 `price_provider=mock` 때문에
`quality runtime preparation requires a cutoff-safe universe price source`로 fail-closed
했다. 저장소 안의 기존 stock-web 자료
`C:\Users\eorb9\projects\news_bot\data\cache\stock-web`를 확인했고,
manifest max date는 2026-06-22였다. 공식 CALIBRATION/HOLDOUT trade date 범위는
2025-12-30부터 2026-06-19까지여서 cutoff-safe D-1 조회 범위에 들어왔다. 설정 파일을
수정하거나 자료를 다운로드하지 않고 해당 경로를 준비 명령 프로세스에만 환경변수로
지정했다.

이 과정에서 source ledger의 `available_before_cutoff=true`는 뉴스 기사 행뿐 아니라
prompt, 원본 CSV 파일, 가격 스냅샷, 거래일, 라우팅 및 일일자료 manifest 메타데이터도
포함한다는 점을 발견했다. 실제 뉴스 행은 `NEWS_CSV_ROW`만 선택하도록 분류했고, 관측한
비뉴스 metadata type은 명시적으로 제외했다. 미지의 cutoff-safe type은 버리지 않고
fail-closed 한다. 일부 뉴스 행은 `published_at_kst` 대신 offset이 붙은 `published_at`을
사용하므로, `time_verified=true`이고 timezone이 실제로 있는 경우에만 fallback을
허용한다. source ledger 80개 전부 공식 SHA-256과 일치했으며 outcome selection/file은
열거나 해시하지 않았다.

공식 blind 입력 준비 결과:

- CALIBRATION: 40 cases, selection ID `QSEL-e3f61fcf722f30e0e7dc`,
  SHA-256 `a9e7f31a18b06988725d5fe63012a0b2b484473422576743151501cfa566ba72`.
- HOLDOUT: 40 cases, selection ID `QSEL-44030751cb3dfdec3b17`,
  SHA-256 `4be25ad5d864a8de2cbc3db1c76d59b3735a0ce9793e086ec9db696027bf1403`.
- 두 split 모두 `outcome_reference_count=0`; sealed case manifest 및 뉴스/D-1 입력
  artifact hash를 40/40 재검증했다.
- 각 split 준비는 약 9분 걸렸고 LLM/OAuth 호출은 0회였다.

실행 중 준비 명령 PID 하나만 10초 간격으로 관찰했다. 두 successful run의 Python
private memory는 약 835–865 MiB, working set은 약 120–149 MiB 사이에서 오르내렸고,
case가 처리되어도 누적 증가하지 않았다. host available RAM 관측 범위는 약
13.4–20.6 GiB여서 8 GiB 경고 기준에 닿지 않았다. 두 Python 프로세스 모두 정상 종료했고
마지막 확인에서 여유 RAM은 20.1 GiB였다. 이는 이 준비 경로의 관측이지 다른 단계의
누수 부재나 1회 전체 compiler peak를 증명하는 것은 아니다.

첫 번째 실패 시도에서 생성된 부분 QINPUT은 삭제하지 않고 보존했다. 공식 split의
잘못된 parent를 사용해 파생했던 오래된 대안 manifest 두 개도 아래 경고처럼 보존하며
공식 평가 입력에 사용하지 않는다.

주의: 검증 중 공식 parent 대신 오래된 대안 split(plan SHA
`cc6fdf0ec99725928121115121568b347be9ff435e0ea9f46c3d005c80c06157`)에서 파생된
CALIBRATION/HOLDOUT source manifest도 각각 하나씩 생성되었다. 두 파일은 outcome 파일
접근, blind input 준비, 예측에 사용하지 않았다. 파일은 삭제하지 않고 보존하며,
공식 evaluation 입력으로 사용하지 않는다.
