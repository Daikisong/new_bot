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
