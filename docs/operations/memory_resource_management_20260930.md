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

compiler 코드에서는 payload 수치와 exposure ledger를 만든 뒤 중복 projected/chunk
계획을 해제하고, leaf capsule 생성 직후 원문 prompt 행을 해제하도록 했다. Reduce는
capsule 및 검증된 child ID만 사용한다. 관련 단위 테스트 14개, Ruff, mypy 139개
source file을 통과했지만, 이 수명 단축의 실 corpus 메모리 절감량은 아직 측정하지 않았다.

재개 시 compiler PID의 private bytes, working set, host available memory를 10초
간격으로 단계/완료 호출 수와 함께 기록한다. 정상적인 단일 단계 피크인지 판단할
수 있도록 같은 단계의 연속 구간을 비교한다. available memory가 8 GiB 아래로
내려가면 경고하고, 6 GiB 아래 상태가 60초 지속되거나 동일 단계에서 private bytes가
계속 증가해 회수 징후가 없으면 checkpoint 보존을 확인한 뒤 중단하고 원인을
분석한다. 한 번의 순간 피크만으로 중단하거나, 반대로 `gc.collect()`만 호출하고
안전하다고 판정하지 않는다. 중단 시 다른 MCP/프로젝트 프로세스는 종료하지 않는다.
