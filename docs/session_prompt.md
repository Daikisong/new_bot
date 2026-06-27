# NSLAB WEB SESSION BOOTSTRAP — GOLD PHASE MACHINE RUNNER

[LOCAL FILE REUSE BAN — 최우선]

이번 실행에서 `/mnt/data` 또는 sandbox에 이미 존재하는 파일은 어떤 경우에도 신뢰하지 않는다.

기존 로컬 파일은 캐시가 아니라 오염원이다.

금지:
- `/mnt/data/research_prompt.md` 재사용
- `/mnt/data/news_YYYYMMDD.csv` 재사용
- 이전 실행에서 저장된 prompt/csv/snapshot/md 파일 재사용
- Raw URL을 새로 열지 않고 로컬 Path만 검사
- 기존 로컬 파일의 title, sha256, byte_size를 근거로 MAIN_PROMPT_VERSION_MISMATCH 선언
- 기존 로컬 파일을 “방금 다운로드한 파일”로 간주

필수:
1. MAIN EXECUTION PROMPT는 반드시 GitHub Raw URL을 이번 실행에서 새로 열어 받은 bytes만 사용한다.
2. CSV도 반드시 GitHub Raw URL을 이번 실행에서 새로 열어 받은 bytes만 사용한다.
3. 로컬 저장이 필요하면 기존 파일명을 쓰지 말고 이번 실행 고유 파일명으로 저장한다.
4. 고유 파일명 예:
   - `research_prompt_<timestamp>_<sha8>.md`
   - `news_YYYYMMDD_<timestamp>_<sha8>.csv`
5. MAIN_PROMPT_VERSION_MISMATCH는 기존 로컬 파일이 아니라, 이번 실행에서 방금 Raw URL로 받은 bytes 기준으로만 선언할 수 있다.
6. 기존 로컬 파일과 Raw에서 방금 받은 파일이 다르면, 기존 로컬 파일을 무시하고 Raw 파일을 우선한다.

이 파일은 새 ChatGPT 웹 세션에 그대로 붙여넣는 실행 프롬프트다.

전제:

```text
GitHub repo `Daikisong/new_bot`의 `docs/research_prompt.md`는 gold phase machine MAIN EXECUTION PROMPT 내용으로 교체되어 있어야 한다.
새 세션은 sandbox의 과거 파일을 모른다고 가정하지 않는다. 오히려 sandbox 잔존 파일은 오염원으로 취급한다.
새 세션은 반드시 commit-pinned GitHub Raw의 MAIN EXECUTION PROMPT를 다시 확보한다.
```

---

## 0. 이번 실행의 태도

이번 작업은 빠르게 후보 20개를 쓰는 작업이 아니다. 정상 거래일이면 다음 순서를 반드시 지킨다.

```text
CSV 전수 확보·전수 분류
→ BLIND 상태에서 천천히 뉴스 연구
→ BLIND 후보 분모 폐쇄
→ BLIND 후보 봉인
→ 그 뒤에만 D 가격 outcome 확인
→ 왜 맞고 틀렸는지 0622 gold처럼 학습 표본으로 분해
→ 두뇌가 먹을 record-level bundle 생성
```

```text
빠른 완료보다 gold 연구 품질이 우선이다.
final 후보부터 만들지 않는다.
가격부터 보지 않는다.
검증 실패는 먼저 population을 수리한다.
얕은 연구에 ACCEPT_FULL을 붙이는 것은 최악의 산출물이다.
```

---

[BOOTSTRAP ACQUISITION RULE — MAIN PROMPT와 CSV 확보 전용]

이 BOOTSTRAP ACQUISITION RULE은 MAIN EXECUTION PROMPT와 news_YYYYMMDD.csv 확보에만 적용하며, stock-web outcome snapshot 다운로드에는 적용하지 않는다. outcome snapshot은 MAIN EXECUTION PROMPT의 BLIND seal 이후 규칙만 따른다.

ChatGPT 세션에서는 Python/bash/container 네트워크가 GitHub DNS를 풀지 못할 수 있다.

다음 오류는 파일 부재가 아니다.

- curl: Could not resolve host
- urllib.request.URLError Temporary failure in name resolution
- socket.gaierror
- NETWORK=caas_packages_only

이 오류만으로 MAIN EXECUTION PROMPT 또는 CSV 확보 실패를 선언하지 않는다.

MAIN EXECUTION PROMPT와 news_YYYYMMDD.csv 모두 다음 순서로 확보한다.

1. web/browser 계층으로 GitHub blob 또는 raw page를 연다.
2. 파일 존재·파일명·Raw/View raw 경로를 확인한다.
3. download tool 또는 파일 분석 도구로 이번 실행 고유 파일명에 저장한다.
4. 로컬 저장 후 sha256·byte_size·전체 parse를 검증한다.
5. shell/curl/urllib 실패 이력은 acquisition_warnings에만 남긴다.
6. 모든 web/download fallback까지 실패한 경우에만 ACQUIRE_FAILED를 선언한다.

다른 날짜 파일, 샌드박스 잔존 파일, 최신 CSV로 대체하지 않는다.

acquisition이 성공하면 파일 확보 검사를 반복하지 않는다. sha256, byte_size, UTF-8 decode, full CSV parse가 통과하면 acquisition은 DONE이며, 즉시 다음 phase로 이동한다.

---

[CSV 순차 실행 설정]

CSV 목록 위치:
https://github.com/Daikisong/new_bot/tree/main/docs/csv

CSV Raw 기본 주소:
https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_YYYYMMDD.csv

이번 세션 시작 파일:
news_YYYYMMDD.csv

`docs/csv`에 존재하는 `news_YYYYMMDD.csv` 파일을 날짜 오름차순으로 정렬한다. 이번 실행에서는 다음 규칙으로 CSV 정확히 하나만 선택한다.

1. 이미 처리된 연구 결과가 있다면 이 대화에서 가장 최근에 처리한 CSV 바로 다음 날짜의 미처리 CSV 하나를 이번 실행 입력으로 사용한다.
2. 이미 처리한 CSV를 다시 사용하지 않는다.
3. 선택한 CSV는 GitHub HTML 미리보기로 읽지 않는다. CSV Raw 기본 주소와 파일명을 결합한 URL에서 실제 파일을 다운로드한 뒤, Python 또는 파일 분석 도구로 전체 행을 파싱한다.
4. GitHub 화면에 “파일이 너무 커서 표시할 수 없다”는 안내가 나와도 파일이 없다고 판단하지 않는다. 반드시 Raw 다운로드를 시도한다.
5. Raw 파일을 실제로 다운로드하거나 전체 파싱하지 못했다면 내용을 추측하여 연구하지 않는다.
6. 한 번의 실행에서는 CSV 하나에 대한 연구 episode 하나만 생성한다.
7. 다음 반복 실행에서는 바로 다음 미처리 CSV 하나를 사용한다.

---

[MAIN EXECUTION PROMPT]

아래 commit-pinned GitHub Raw URL의 실행 프롬프트 전체를 열고 그대로 이행한다.

https://raw.githubusercontent.com/Daikisong/new_bot/de91255baaa2124af747fffdaf571202b03fc07c/docs/research_prompt.md

이 URL은 commit-pinned Raw URL이다.

branch main URL, GitHub HTML preview, 기존 로컬 파일로 대체하지 않는다.

방금 Raw URL에서 받은 MAIN EXECUTION PROMPT는 반드시 다음 값이어야 한다.

expected_title:
```text
# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER
```

expected_sha256:
```text
1e593b0238733b4501ff1585335f45ff0e7e4745dd085b2d891344aa65f01ba6
```

expected_byte_size:
```text
382408
```

필수 확인:

```text
MAIN EXECUTION PROMPT 상단 제목이 `NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER`이거나,
research_prompt_revision에 `nslab.gold_phase_machine.direct_csv_research.locked`가 있어야 한다.
```

MAIN_PROMPT_VERSION_MISMATCH Markdown은 오직 “이번 실행에서 commit-pinned Raw URL로 방금 받은 bytes”가 위 expected_title 또는 expected_sha256과 다를 때만 생성할 수 있다.

기존 `/mnt/data/research_prompt.md`, sandbox 잔존 파일, 이전 다운로드 파일, branch main 캐시 파일을 검사한 결과로는 MAIN_PROMPT_VERSION_MISMATCH를 생성할 수 없다.

expected 값이 맞으면 즉시 MAIN PROMPT acquisition은 DONE으로 처리하고, prompt 파일 검사를 반복하지 말고 CSV 확보 단계로 이동한다.

만약 expected 값이 다르면 기존 로컬 파일 또는 캐시된 파일을 본 것이다. 이 경우 MAIN_PROMPT_VERSION_MISMATCH를 만들지 말고, 기존 로컬 파일을 무시하고 commit-pinned Raw URL을 다시 연다. 모든 web/browser/download fallback으로 commit-pinned Raw bytes를 새로 확보하지 못한 경우에만 ACQUIRE_FAILED를 선언한다.

---

[최우선 날짜·거래일 라우팅 규칙]

1. 파일명의 YYYYMMDD를 연구 대상 달력 날짜 D의 후보로 사용하되, CSV 내부 게시시각과 한국 주식시장의 공식 거래일 여부를 함께 확인해 확정한다.

2. D가 실제 거래일이면 현재 CSV 하나를 완결된 연구 입력으로 사용한다.

현재 CSV는 수집 단계에서 이미 아래 전체 구간을 포함하는 것으로 간주한다.

```text
직전 실제 거래일 P의 15:30:00 KST
~
현재 거래일 D의 08:59:59 KST
```

따라서 월요일, 연휴 다음 거래일에도 토요일·일요일·공휴일 CSV 또는 앞서 생성된 DEFERRED Markdown을 별도로 병합하지 않는다.

예:

```text
월요일 거래일 D의 CSV
= 직전 금요일 15:30:00 ~ 월요일 08:59:59

연휴 다음 거래일 D의 CSV
= 연휴 직전 실제 거래일 15:30:00 ~ 연휴 다음 실제 거래일 08:59:59
```

3. 거래일 CSV의 실제 최소·최대 게시시각을 확인해 위 구간을 충분히 포함하는지 검사한다. 범위가 일부 부족하거나 시간 검증이 불가능한 행이 있더라도 연구를 중단하지 말고 다음 필드에 명시한 뒤 가능한 데이터로 계속 진행한다.

```text
input_coverage_warning
uncovered_time_ranges
time_unverified_rows
```

4. 공식 거래일인데 stock-web에 D 가격 데이터가 없다는 이유만으로 휴장일로 처리하지 않는다.

이 경우:

```text
status = PRICE_SOURCE_MISSING
blind_valid = true
```

로 기록하고 BLIND 연구와 봉인은 정상 수행하되, 가격 결과가 필요한 POSTMORTEM만 보류한다.

5. D가 공식 비거래일이면 일반 BLIND 분석, 후보 예측, 가격 결과 조회, POSTMORTEM, Brain Delta 생성을 수행하지 않는다.

6. 공식 비거래일에는 실제 다운로드 가능한 최소 Markdown 파일 하나를 생성한다.

파일명:

```text
<CALENDAR_DATE>_nslab_deferred_non_trading_<INPUT_SHA8>.md
```

파일에는 최소한 다음 내용을 기록한다.

```text
schema_version: nslab.deferred_input.v1
artifact_type: deferred_non_trading_day
status: DEFERRED_NON_TRADING_DAY
brain_eligible: false
outcome_research_performed: false
merge_required: false
covered_by_next_trading_day_csv: true
input_file
input_sha256
calendar_date
previous_trade_date
next_trade_date
created_at
reason
```

previous_trade_date 또는 next_trade_date를 신뢰할 수 있게 확인하지 못하면 추측하지 말고 null로 기록한다.

7. 비거래일 CSV와 비거래일 Markdown은 이후 거래일 연구에 직접 병합하거나 뉴스 근거로 사용하지 않는다.

8. 공식 거래일이면 MAIN EXECUTION PROMPT를 정상적으로 전부 실행하고, 표준 단일 episode bundle Markdown 하나를 생성한다.

9. 거래일과 비거래일 모두 반드시 실제 다운로드 가능한 Markdown 파일 하나를 생성한다. 채팅 코드블록으로 파일 내용을 대신하지 않는다.

---

[품질 잠금 — 빠른 종료 방지]

정상 거래일 + outcome source available이면 다음이 끝나기 전에는 final file을 생성하지 않는다.

```text
1. source_ledger row-level count == csv_row_count
2. row_disposition count == csv_row_count
3. material_review_queue_count == material_reviewed_count
4. candidate_screening covers all material observations and rejected/watch/audit-only cases
5. final_watchlist is created only after candidate population closes
6. blind_seal_receipt is written and verified before any outcome byte/header/hash/row access
7. outcome_ledger full-market rows parsed
8. outcome_leader_census covers all upper-limit/high20/high15/high10 policy leaders
9. outcome_to_news_audit is 1:1 with outcome_leader_census and contains no final scorecard rows
10. postmortem supervised populations are built
11. brain_delta uses record_type records, not lesson memo rows
12. blind_report has sections 1~19, postmortem_report has sections 20~36
13. final Markdown is re-opened and parsed for validation before ACCEPT_FULL
```

만약 위 조건 중 하나가 실패하면 먼저 수리한다. 단순히 `ACCEPT_FULL`을 금지하는 것으로 끝내지 말고, 누락된 row/source/candidate/outcome/brain_delta population을 생성한 뒤 재검증한다.

---

[채팅 출력 규칙]

연구가 끝나기 전까지 중간 설명, 진행상황, 표, 요약을 채팅에 출력하지 않는다.

최종 채팅 응답은 정확히 아래 한 줄만 남긴다.

```text
파일명: <YYYYMMDD>_nslab_episode_bundle.md
```
