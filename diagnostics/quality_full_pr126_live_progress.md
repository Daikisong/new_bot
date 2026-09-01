# QUALITY_FULL PR126 실시간 진행 스냅샷

## 중단·대체 공지

이 문서는 2026-09-02 01:22 KST의 역사적 시점 스냅샷이며 더는 현재 진행
상태가 아니다. 해당 worker는 완료 pack 5개에서 중단됐고 전체 ancestry는
`HALTED_MISALIGNED_DIAGNOSTIC_ONLY`로 분류됐다. 하루치 CSV에 379개 대형 LLM
호출을 사용하는 경로가 “일회성 brain 구축 후 08시 CSV는 기존 brain으로
판단한다”는 제품 의도와 어긋났기 때문이다.

재개·채점·비교·승격을 금지한다. 현재 판정은
`diagnostics/quality_full_misaligned_runtime_report.{json,md}`, 원래 제품 의도는
`docs/operations/one_time_brain_daily_inference_intent.md`를 따른다.

## 판정

이 문서는 `2026-09-02T01:22:58.9005034+09:00` 시점의 고정 스냅샷이다.
실시간 카운터가 아니므로 이후 완료된 pack은 다음 스냅샷에서 추가한다.

```text
상태                       HISTORICAL_RUNNING_NOT_SCORED_SUPERSEDED
production 활성화          NOT_PRODUCTION_ACTIVATED
브랜치                     codex/quality-full-pr126
감사 대상 구현 커밋         5fdbcfe594954eb8e901bf65847af5563852ef14
paired prediction run      QPRED-704f15cde6e4152b6931
현재 case                  NSLAB-20260102-be50ec83
현재 variant               V1
현재 runtime run           RUN-9701018d4a4e
```

이 시점에는 case 1의 V0만 봉인됐다. V1은 실행 중이고, 전체 3 case x 2
variant의 예상 seal 6개 중 1개만 존재한다. `paired_case_ids`는 비어 있으며,
outcome은 열리지 않았고 score도 없다. 따라서 예측 품질, V1 우위, compiler
v8 착수, production 승인을 주장할 수 없다.

## 지금 하는 작업

현재 작업은 모델 학습, 연구 import, brain rebuild가 아니다. 평가 시점의
RAG 근거 소화 작업이다.

1. 2026-01-02 장전 뉴스 490건에서 생성된 478개 material event cluster마다
   cutoff-safe 과거 record를 검색했다.
2. 검색 결과의 49,984개 `(cluster_id, record_id, lane)` 관계를 보존했다.
3. 중복 payload를 제거해 고유 record 1,560개를 1,914회만 직렬화했다.
4. 이 관계와 payload를 최대 240,000자 prompt 379개로 pack했다.
5. 각 pack을 `gpt-5.6-sol/xhigh`가 읽고, 현재 cluster에 대한 지지·반례·
   near-miss·희귀 메커니즘 evidence memo를 구조화 출력한다.
6. 모든 pack이 끝나면 memo가 case 1 V1의 최종 테마·수혜주·대장주 판단에
   들어간다. V1 seal 뒤에도 outcome은 열지 않으며, case 2·3의 V0/V1까지
   모두 봉인한 뒤에만 별도 scoring 명령이 outcome을 연다.

즉 현재 379개 pack은 production brain을 만드는 일이 아니라, “과거 연구를
실제로 읽힌 V1이 과거 연구를 읽히지 않은 V0보다 예측을 개선하는가”를
검증하기 위한 case 1의 하위 단계다.

## 진행률 해석

```text
pack plan                  379
완료                       4
남음                       375
현재 pack 단계 완료율       1.0554%
현재 pack 단계 잔여율       98.9446%
```

이 백분율은 **case 1 V1 runtime evidence pack 단계만의 수치**다. 전체 goal의
완료율이나 3-case 전체 평가 완료율이 아니다. 이후에도 case 1 최종 합성,
case 2·3 V0/V1, 3-case score, CALIBRATION40, compiler v8/V2, HOLDOUT40,
post-cutoff shadow가 남아 있다. 아직 전체 goal을 정직한 단일 백분율로
환산할 근거는 없다.

## 왜 이틀이 걸렸는가

최근 이틀 전체를 4개 pack 생성에 사용한 것이 아니다. 먼저 다음 상태를
만들고 검증했다.

- 세 case의 shared pre-retrieval context 봉인
- case 1 V0 예측과 seal
- 478개 cluster의 runtime retrieval trace와 재개 sidecar
- 약 3.7GB population/representative artifact 및 70GiB 수준의 재시작 검증 I/O
- 49,984개 관계를 누락하지 않는 cross-cluster pack plan
- 대규모 daily context의 bounded compact v2와 hash commitment
- Pydantic `ValueError` 문맥이 내부 structured repair를 막던 직렬화 오류 수정
- Ruff, Mypy, 전체 Pytest 1,863개 통과

실제 379-pack plan은 2026-09-01 22:45 KST에 고정됐다. 완료된 첫 4개 pack의
관측 소요는 약 13.0, 34.3, 29.6, 14.0분이다. 중앙값 21.8분을 단순 적용하면
남은 375개는 약 136시간, 5.7일이다. 이는 순차 실행 관측치일 뿐 보장이나
중단 gate가 아니다. structured repair, provider 실패, PC 중단, 재시작 deep
검증에 따라 더 길어질 수 있고 case 2·3 시간은 포함하지 않는다.

## 누락 방지와 압축 효과

```text
assignment count                 49,984
unique record count               1,560
unpacked payload occurrences     49,984
planned payload occurrences       1,914
avoided duplicate occurrences    48,070
first-N shortcut                  false
silent truncation                 false
assignment root                  5f852183...3fe12d
pack plan SHA-256                da02ea1a...f24858b6e
```

49,984개의 관계를 LLM payload 49,984회로 반복 전송하는 대신 실제 record
payload는 1,914회만 전송한다. 그러나 각 관계의 cluster와 lane 귀속은 plan에
남긴다. 이것이 현재 379개 호출의 목적이며, record를 임의로 앞부분만 잘라
쓰는 작업이 아니다.

## 완료 pack

| 순서 | Pack | 출력 SHA-256 | Checkpoint | 상태 |
| ---: | --- | --- | --- | --- |
| 1 | `REPACK-944afc84f53ea6004236` | `ef59ad17...24b27f5` | `LLMCKPT-6aaf7b8fbd41c426` | ok |
| 2 | `REPACK-42e3c0dbfb182600b7d3` | `17c2ef6f...e140326` | `LLMCKPT-fa9ee96cfb38c3f9` | ok |
| 3 | `REPACK-b69f594789245ca709e9` | `bfb2d72a...3e96e7` | `LLMCKPT-fe520dbf446fa913` | ok |
| 4 | `REPACK-57cee5def1bd00c21d82` | `180bb9b0...37661c0` | `LLMCKPT-51513b5becbcca0f` | ok |

관측 시점에는 다섯 번째 `REPACK-220549cfef6a0e0b9d09`가 실행 중이었다.
완료된 pack만 content-addressed `ok` checkpoint로 재사용하도록 설계돼 있었다.
중단된 live call은 완료로 세지 않았지만, 현재 이 ancestry 전체가 무효화돼
동일 plan 재개 자체를 금지한다.

## 핵심 commitment

| Artifact | SHA-256 |
| --- | --- |
| blind runtime selection | `bfcc21e433ab36cf457254f7587f7fb71d2a12e4a332a12c0fead0b0554d3f9c` |
| paired prediction manifest | `bfb9b8a0923300c77b46d82e74ab0a4375d09aa68ce57775ac827f409815686b` |
| compact final context | `4d86b799a2577c16785866354025d89e0401765df2e3c92ad25e423e005d0171` |
| full daily memory context | `ad3cf2ed69e720f6ae448e5e933c6dd90b861691b29ef243e808f40138121e5b` |
| runtime evidence pack plan | `da02ea1a4e63e114d822d95c61af4fa08e8bfcfe3b04d5cb16394f6f24858b6e` |

전체 값, 파일 크기, pack별 hash는
`diagnostics/quality_full_pr126_live_progress.json`에 있다.

## GitHub 감사 범위

GitHub에서 직접 검토 가능한 것은 구현 코드, 테스트, 이 commitment 보고서,
외부 리뷰 brief다. 대용량 평가 프로젝트, 1.2MB pack plan, daily context,
pack 출력, OAuth checkpoint payload는 repository에 넣지 않았다.

따라서 외부 리뷰는 두 단계로 나뉜다.

1. GitHub branch에서 blind boundary, checkpoint identity, pack 완전성 검증,
   structured repair, 테스트 범위를 코드 리뷰한다.
2. 실제 artifact 감사가 필요하면 JSON에 적힌 로컬 경로의 파일을 별도
   export하고 SHA-256을 대조한다.

GitHub 문서만으로 predictive quality를 승인하거나 “379개가 모두 완료됐다”고
판정하면 안 된다.

## 남은 순서

```text
PR126  case 1 V1 pack/final synthesis/seal
       case 2 V0/V1 seal
       case 3 V0/V1 seal
       6/6 seal 뒤 outcome open 및 3-case score
PR127  CALIBRATION40 V0/V1
PR128  semantic compiler v8 및 V2 evaluation brain
PR129  HOLDOUT40 V0/V1/V2
PR130  post-cutoff forward shadow 및 final brain candidate
Final  production activation은 계속 HOLD
```

## 외부 리뷰 질문

1. 49,984개 assignment가 plan root와 완료 manifest에서 정확히 폐쇄되는가?
2. record payload 중복 제거가 cluster/lane 관계를 잃지 않는가?
3. prompt bound가 first-N 또는 silent truncation 없이 지켜지는가?
4. `ok`가 아닌 checkpoint가 성공으로 재사용될 수 있는 경로가 있는가?
5. prediction process가 6/6 seal 전에 outcome을 resolve, stat, hash, open할 수 있는가?
6. pack memo의 record provenance가 V1 최종 citation까지 폐쇄되는가?
7. 현재 관측 비용을 줄이면서도 위 완전성 조건을 그대로 지킬 추가 구조가 있는가?
