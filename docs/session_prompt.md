<!--
유지보수 메모:
docs/research_prompt.md가 바뀌면 이 세션 프롬프트의 MAIN EXECUTION PROMPT 섹션도 함께 갱신한다.
반드시 main branch Raw URL, expected_sha256, expected_byte_size를 새 research_prompt.md 기준으로 맞춘다.
웹 세션에 붙여넣기 전에는 `이번 세션 시작 파일: news_YYYYMMDD.csv`만 실행할 CSV 파일명으로 바꾼다.
-->

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
새 세션은 반드시 main branch GitHub Raw의 MAIN EXECUTION PROMPT를 다시 확보한다.
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

[CSV RESEARCH WORKBENCH LOCK — 키워드·스크립트 shortcut 금지]

Python 또는 파일 분석 도구는 금지되지 않는다. 단, 도구는 독자가 아니라 서기다.

허용:
- sha256, byte_size, UTF-8 decode, row_count, column schema 확인
- published_at 정렬, row_id 부여, 중복·누락 검사
- source_ledger, row_disposition, material_review_queue를 전수 생성하기 위한 navigation index 작성
- 이미 닫힌 population을 재검증하기 위한 count/hash 계산

금지:
- Python 출력 목록을 “CSV를 읽었다”는 증거로 사용
- 처음 250개, 300개, 일부 chunk 제목 출력만 보고 뉴스 흐름을 판단
- 제목 출력 길이를 줄이거나 늘리는 작업을 연구 진행으로 간주
- 회사명 keyword hit list, mention count, grep 결과를 candidate population으로 사용
- 일반명사와 충돌하는 회사명, 짧은 영문 ticker-like token, substring match를 후보 근거로 사용
- “제목이 회사명으로 시작하는 경우만 자료로 설정” 같은 임시 휴리스틱으로 issuer gate를 대체
- keyword list를 먼저 만들고 그 목록에 맞춰 후보를 수렴
- P snapshot ranking/amount/return 정보를 direct issuer catalyst로 둔갑
- final 후보를 먼저 만든 뒤 source_ledger, row_disposition, fact_ledger, candidate_screening을 끼워맞춤

필수:
- CSV row 전체는 반드시 source_ledger와 row_disposition에 먼저 닫힌다.
- material 판단은 row text를 읽고 exact quote, local predicate owner, issuer binding, rejection reason으로 기록한다.
- material_review_queue 전체가 reviewed 상태가 되기 전에는 candidate_screening을 닫지 않는다.
- candidate_screening은 keyword hit list가 아니라 material_review_queue 전체를 연구한 결과여야 한다.
- 후보마다 source row, exact quote, fact_id, inference_id, semantic witness가 있어야 한다.
- chunk를 나눠 읽는 경우에도 각 chunk의 목적은 제목 감상이 아니라 row_disposition/material_review_queue population 작성이다.
- issuer가 불명확하거나 일반명사·행사명·타사 기사·표/목록 구성원에서만 등장한 경우에는 후보가 아니라 rejection/audit-only record로 남긴다.

---

[PHASE 1 FIRST OUTPUT CONTRACT — 후보 진입 전 분모 폐쇄 강제]

CSV full parse가 끝난 직후 다음 유효 작업은 반드시 `source_ledger`와 `row_disposition` 전수 population 생성이다.

그 전에는 금지:
- 후보명, 종목코드, 테마, watchlist 작성
- P snapshot rank, amount, return 확인
- “중요 행 추출”, “대표 기사 추출”, “상위 250개/300개 제목 검토”
- 작업 크기, 처리량, 수동 검토 불가능성, 완벽성 한계에 대한 meta-planning
- 회사명, 키워드, ticker-like token으로 먼저 후보를 좁히는 작업

PHASE 1 통과 조건:
```text
source_ledger_count == csv_row_count
row_disposition_count == csv_row_count
material_review_queue_count == material_reviewed_count
```

위 count receipt를 산출물 내부에 쓰기 전에는 `candidate_screening`, `final_watchlist`, 종목코드 매핑, P snapshot ranking으로 이동할 수 없다.

---

[NO META-PLANNING ESCAPE — 작업 축소 사고 금지]

다음 판단은 연구 진행이 아니라 품질 위반이다.

```text
작업이 너무 크다
모든 행을 수동으로 읽을 수 없다
완벽할 필요는 없다
효율적으로 중요한 행만 추출한다
회사명/키워드로 먼저 후보를 좁힌다
일단 후보를 만들고 나중에 ledger를 맞춘다
```

이런 생각이 나오면 해당 사고를 즉시 폐기하고 PHASE 1 population 작성으로 돌아간다. 이 문구를 산출물에 변명처럼 기록하지 말고, 실제 작업 순서를 `source_ledger` → `row_disposition` → `material_review_queue` 폐쇄로 되돌린다.

---

[ANTI-REWARD-HACK VALIDATION CONTRACT — 파일 재파싱 기반 검증]

아래 조건은 설명 문구가 아니라 run 유효성 조건이다. 하나라도 위반하면 해당 episode는 `ACCEPT_FULL`이 아니라 invalid다.

1. `final_watchlist`, `final_codes_order`, `selected_codes`, `top_codes`, `manual_ticker_list`처럼 최종 후보 ticker 목록을 코드나 리스트로 먼저 선언하지 않는다.
2. `final_watchlist`는 반드시 저장된 `candidate_screening.jsonl`을 다시 열어 parse한 결과에서만 생성한다. in-memory 후보 변수나 미리 정한 ticker 순서에서 생성하지 않는다.
3. `material_reviewed=true`는 `material_review_queue_member`와 같은 boolean으로 자동 부여할 수 없다. 각 material row마다 `review_decision`, `exact_quote`, `issuer_binding` 또는 `rejection_reason`이 있어야 한다.
4. validation actual 값은 in-memory 변수 사용 금지다. 반드시 저장된 `JSONL`/`JSON`/`Markdown` artifact를 다시 열어 parse한 값만 actual로 쓴다.
5. count receipt가 맞아도 아래 evidence chain이 끊기면 invalid다.

```text
source_row
→ row_disposition
→ material_review
→ fact
→ inference
→ candidate_screening
→ final_watchlist
```

`final_watchlist`의 각 item은 위 chain의 모든 id를 거꾸로 따라가서 원본 CSV row와 exact quote까지 도달해야 한다. 도달하지 못하면 filler 후보로 간주하고 제거한다.

---

[BOOTSTRAP ACQUISITION RULE — MAIN PROMPT와 CSV 확보 전용]

이 BOOTSTRAP ACQUISITION RULE은 MAIN EXECUTION PROMPT와 news_YYYYMMDD.csv 확보에만 적용하며, stock-web outcome snapshot 다운로드에는 적용하지 않는다. outcome snapshot은 MAIN EXECUTION PROMPT의 BLIND seal 이후 규칙만 따른다.

MAIN EXECUTION PROMPT와 news_YYYYMMDD.csv 확보의 primary path는 shell/curl/urllib가 아니다.

primary path는 다음 하나다.

```text
web/browser로 Raw URL 열기
→ download tool 또는 파일 분석 도구가 이번 실행 Raw bytes를 temp file로 저장
→ shell은 저장된 temp file의 sha256/byte_size/header/full parse만 검증
→ 검증 통과 시 sha8이 붙은 고유 파일명으로 rename
→ acquisition DONE
```

반드시 이번 실행 전용 run directory를 만든다.

```text
/mnt/data/nslab_run_<timestamp>/
```

MAIN PROMPT와 CSV는 먼저 같은 run directory 안의 temp 이름으로 저장한다.

```text
prompt_tmp.md
news_YYYYMMDD_tmp.csv
```

download/file-analysis tool이 파일을 다른 `/mnt/data` 위치에 저장했다면, 그 파일이 이번 실행에서 방금 Raw URL로 받은 파일임을 확인한 뒤 즉시 current run directory로 옮기거나 복사한다. 기존 고정 파일명이나 다른 run directory의 파일은 사용하지 않는다.

검증 명령은 shell을 사용해도 된다. 단, shell은 다운로드 수단이 아니라 검증 수단이다.

```text
sha256sum prompt_tmp.md
wc -c prompt_tmp.md
head -n 1 prompt_tmp.md

sha256sum news_YYYYMMDD_tmp.csv
wc -c news_YYYYMMDD_tmp.csv
python/csv full parse
```

검증 통과 후 rename 예:

```text
research_prompt_<timestamp>_<sha8>.md
news_YYYYMMDD_<timestamp>_<sha8>.csv
```

ChatGPT 세션에서는 Python/bash/container 네트워크가 GitHub DNS를 풀지 못할 수 있다. 따라서 shell/curl/urllib/requests는 primary download path로 사용하지 않는다.

다음 오류는 파일 부재가 아니다.

- curl: Could not resolve host
- urllib.request.URLError Temporary failure in name resolution
- socket.gaierror
- NETWORK=caas_packages_only

이 오류만으로 MAIN EXECUTION PROMPT 또는 CSV 확보 실패를 선언하지 않는다.

curl/urllib/requests를 시도했다가 DNS 실패가 나오면 같은 URL에 대해 반복하지 않는다. 실패 이력은 acquisition_warnings에만 남기고 즉시 web/browser + download/file-analysis primary path로 돌아간다.

모든 web/browser + download/file-analysis fallback까지 실패한 경우에만 ACQUIRE_FAILED를 선언한다.

다른 날짜 파일, 샌드박스 잔존 파일, 최신 CSV로 대체하지 않는다.

acquisition이 성공하면 파일 확보 검사를 반복하지 않는다. sha256, byte_size, UTF-8 decode, full CSV parse가 통과하면 acquisition은 DONE이며, 즉시 다음 phase로 이동한다.

acquisition DONE 이후에는 다운로드 방법, 파일 접근 전략, 작업 크기, 처리량 축소 여부를 논의하지 않는다. 즉시 PHASE 1의 source_ledger, row_disposition, material_review_queue population으로 이동한다.

---

[STOCK-WEB ACQUISITION FAST PATH — 가격 계층 전용]

이 규칙은 stock-web `research_daily` manifest/schema/access JSON과 blind/outcome snapshot CSV 확보에만 적용한다. MAIN EXECUTION PROMPT와 news CSV 확보 규칙을 대체하지 않는다.

stock-web manifest/schema/access JSON은 routing metadata 확인용이다. 실제 가격 provenance는 snapshot CSV Raw bytes로 닫는다.

반드시 다음 순서를 따른다.

```text
1. stock-web manifest/schema/access JSON은 shell/curl/urllib/requests로 받으려 하지 않는다.
2. web/browser로 Raw JSON을 열어 blind_snapshot_path, outcome_snapshot_path, row_count, sha256 metadata만 확인한다.
3. JSON bytes를 정확히 저장하지 못했으면 sha256을 조작하거나 JSON을 복원하지 않는다.
   access_sha256_status = WEB_VIEW_ONLY_UNHASHED
   acquisition_warnings에 남긴다.
4. access JSON의 역할은 blind_snapshot_path/outcome_snapshot_path 확인까지만이다.
5. 실제 가격 provenance는 snapshot CSV Raw bytes로 닫는다.
6. BLIND 전에는 blind_snapshot_path만 다운로드한다.
7. outcome_snapshot_path는 blind_seal_receipt 생성/검증 후에만 다운로드한다.
8. snapshot CSV는 반드시 temp file로 저장한 뒤 sha256, byte_size, header, row_count를 검증한다.
9. JSON 다운로드 실패를 이유로 snapshot CSV 확보를 지연하지 않는다.
10. JSON 내용을 사람이 복사해서 새 JSON 파일로 재구성하지 않는다.
```

stock-web JSON에서 DNS, content-type, download tool 제한이 발생하면 같은 URL에 대해 shell/curl/urllib 재시도를 반복하지 않는다. 해당 실패는 `acquisition_warnings`에 남기고, web/browser로 확인한 snapshot CSV Raw path 확보로 이동한다.

BLIND 전 허용:

```text
research_daily manifest/schema/access JSON routing metadata 확인
previous_trade_date 또는 blind_snapshot_path CSV Raw bytes 다운로드·검증
```

BLIND 전 금지:

```text
outcome_snapshot_path CSV bytes/header/hash/row 접근
D-day high_return, amount_rank, winner, upper_limit 결과 접근
access JSON을 손으로 복원해 sha256을 만든 척하기
```

---

[CSV 순차 실행 설정]

CSV 목록 위치:
https://github.com/Daikisong/new_bot/tree/main/docs/csv

CSV Raw 기본 주소:
https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_YYYYMMDD.csv

이번 세션 시작 파일:
news_YYYYMMDD.csv

이번 실행에서는 CSV 목록을 탐색하지 않는다. `이번 세션 시작 파일:`에 지정된 CSV 정확히 하나만 사용한다.

1. 지정된 CSV를 다른 날짜 CSV, 최신 CSV, 샌드박스 잔존 CSV로 대체하지 않는다.
2. 지정된 CSV는 GitHub HTML 미리보기로 읽지 않는다.
3. CSV Raw 기본 주소와 파일명을 결합한 URL에서 실제 Raw bytes를 확보한다.
4. GitHub 화면에 “파일이 너무 커서 표시할 수 없다”는 안내가 나와도 파일이 없다고 판단하지 않는다. 반드시 Raw download/file-analysis path를 사용한다.
5. Raw 파일을 실제로 다운로드하거나 전체 파싱하지 못했다면 내용을 추측하여 연구하지 않는다.
6. 한 번의 실행에서는 지정된 CSV 하나에 대한 연구 episode 하나만 생성한다.

---

[MAIN EXECUTION PROMPT]

아래 main branch GitHub Raw URL의 실행 프롬프트 전체를 열고 그대로 이행한다.

https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md

이 URL은 main branch Raw URL이다.

commit-pinned URL, GitHub HTML preview, 기존 로컬 파일로 대체하지 않는다.

방금 Raw URL에서 받은 MAIN EXECUTION PROMPT는 반드시 다음 값이어야 한다.

expected_title:
```text
# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER
```

expected_sha256:
```text
0c2d11214bdebb6d0452c967307705cfb6731d41480ea5f9c4194e397e66850e
```

expected_byte_size:
```text
409989
```

필수 확인:

```text
MAIN EXECUTION PROMPT 상단 제목이 `NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER`이거나,
research_prompt_revision에 `nslab.gold_phase_machine.direct_csv_research.locked`가 있어야 한다.
```

MAIN_PROMPT_VERSION_MISMATCH Markdown은 오직 “이번 실행에서 main branch Raw URL로 방금 받은 bytes”가 위 expected_title 또는 expected_sha256과 다를 때만 생성할 수 있다.

기존 `/mnt/data/research_prompt.md`, sandbox 잔존 파일, 이전 다운로드 파일을 검사한 결과로는 MAIN_PROMPT_VERSION_MISMATCH를 생성할 수 없다.

expected 값이 맞으면 즉시 MAIN PROMPT acquisition은 DONE으로 처리하고, prompt 파일 검사를 반복하지 말고 CSV 확보 단계로 이동한다.

만약 expected 값이 다르면 기존 로컬 파일 또는 캐시된 파일을 본 것이다. 이 경우 MAIN_PROMPT_VERSION_MISMATCH를 만들지 말고, 기존 로컬 파일을 무시하고 main branch Raw URL을 다시 연다. 모든 web/browser/download fallback으로 main branch Raw bytes를 새로 확보하지 못한 경우에만 ACQUIRE_FAILED를 선언한다.

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
