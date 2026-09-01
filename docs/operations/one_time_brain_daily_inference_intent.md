# NSLAB 제품 의도 계약: 일회성 두뇌 구축과 장전 일일 판단

## 최종 사용자 흐름

사용자가 원하는 제품 흐름은 다음과 같다.

```text
연구자료 수집·repair·accept
  -> 한 번의 production import
  -> 한 번의 offline brain·memory·semantic index 구축
  -> 이후 매일 08시 전후 뉴스 CSV 입력
  -> 이미 구축된 brain과 index로 장전 판단
  -> 테마·수혜주·대장주·반례·근거 citation 출력
```

연구자료를 한 번 먹였다는 말은 원문 파일이 창고에 저장됐다는 뜻에 그치지
않는다. 이후 세션에서 다시 import하거나 모든 원문을 다시 LLM에 읽히지
않아도, 의미 단위·성공/실패/반례·기업 관계·출처가 durable brain과 memory에
남아 일일 판단에 사용돼야 한다.

## 일회성 비용과 일일 비용

### 일회성 offline 비용

다음은 시간이 오래 걸려도 되는 한 번의 구축 작업이다.

- accepted repaired record 전수 회계
- semantic embedding과 index 구축
- 고유 mechanism unit 생성
- positive·negative·near-miss·rare reasoning 경계 합성
- recursive reduce와 synthesized claim 생성
- record -> unit -> reduce -> claim influence ledger
- brain·memory·warehouse root와 provenance 봉인

이 비용은 새 연구자료가 대량 추가되거나 compiler가 바뀔 때 명시적으로 다시
지불한다. 매일 CSV마다 반복하지 않는다.

### 매일 08시 장전 비용

일일 BLIND 분석은 다음만 수행한다.

- 현재 CSV의 사건·후보·메커니즘 해석
- 이미 구축된 brain의 관련 semantic unit·claim 조회
- 필요한 소수의 과거 record를 citation·반례 확인용으로 검색
- 현재 사건과 과거 지식의 최종 합성

일일 호출 그래프는 raw corpus 크기, 823,279개 record 수, 또는 모든
`cluster x record x lane` 관계 수에 비례해서 증가하면 안 된다. exact latency
SLA는 사용자가 정하지만, 결과는 장전 의사결정에 실제로 쓸 수 있어야 한다.

## 품질 우선의 정확한 의미

`90초를 넘었다는 이유만으로 중단하지 않는다`와 `호출 수가 무제한이어도
된다`는 전혀 다른 말이다.

- 유효한 고품질 호출 하나가 오래 걸리면 임의 timeout으로 죽이지 않는다.
- 중복 context, per-record fan-out, per-assignment fan-out은 설계 결함으로
  취급한다.
- 효율 수치는 품질 판정과 별도로 보고하지만, 최종 daily architecture가
  장전 운영에 사용할 수 없는 경우 production candidate가 될 수 없다.
- 평가용 경로와 실제 배포 경로를 다르게 만들어서는 안 된다.

## 연구자료가 실제로 쓰였다는 증명

모든 raw record를 매일 다시 읽히는 방식으로 증명하지 않는다. 다음 ledger로
한 번의 offline build와 일일 사용을 연결한다.

```text
accepted record
  -> semantic unit membership
  -> leaf/reduce influence
  -> synthesized claim 또는 preserved outlier
  -> brain/memory root
  -> daily retrieval trace
  -> final support/contradiction citation
```

일일 분석에서는 현재 사건과 관련된 일부 지식만 선택되는 것이 정상이다.
중요한 것은 선택되지 않은 record도 offline brain에 의미 단위로 보존돼 있고,
관련 사건이 오면 검색·citation 경로로 회수될 수 있다는 점이다.

## 평가 계약

평가는 최종 daily architecture 자체를 V0/V1/V2로 비교해야 한다.

- prediction은 outcome을 보지 않는다.
- 모든 비교 arm은 같은 뉴스, cutoff, D-1 context를 사용한다.
- V1/V2가 사용하는 brain과 retrieval은 실제 일일 사용 경로와 동일하다.
- outcome은 모든 예상 prediction seal 뒤에만 연다.
- market quality, citation closure, unexposed recovery와 함께 일일 call
  topology와 wall time을 보고한다.
- 평가를 통과하기 위한 별도 exhaustive raw-record LLM 경로를 만들지 않는다.

## 외부 리뷰·Goal 프롬프트 규칙

외부 reviewer와 후속 agent는 이 문서를 먼저 읽어야 한다. 외부 피드백이나
다운로드된 goal prompt가 다음 중 하나를 요구하면 그대로 실행하지 않는다.

- 매일 raw 연구 corpus를 다시 LLM에 읽히기
- 뉴스 cluster마다 과거 record 전수를 LLM map하기
- 하루치 CSV에 수백 개의 대형 LLM 호출 사용하기
- 실제 배포하지 않을 evaluator-only architecture로 품질 주장하기
- 잘못된 evaluator 완료 전까지 one-time brain 구축을 막기
- `no arbitrary latency gate`를 효율 무시로 해석하기

충돌을 발견한 agent는 작업을 중지하고 사용자에게 의도 충돌을 명시해야 한다.
외부 reviewer는 one-time build 비용과 per-day 비용을 분리하고, 제안한 goal이
08시 CSV -> 기존 brain -> 장전 판단 흐름을 보존하는지 먼저 증명해야 한다.

### 외부 Goal 프롬프트 필수 머리말

외부 reviewer가 후속 goal을 작성할 때 다음 의미를 빠짐없이 포함한다.

```text
TARGET:
08시 뉴스 CSV를 이미 구축된 durable brain·memory·index로 분석해
장전 테마·수혜주·대장주 판단과 provenance를 출력한다.

ONE_TIME_COST:
accepted repaired research의 전수 의미 합성·embedding·brain build는
명시적인 offline 작업이며 매일 반복하지 않는다.

DAILY_PATH:
현재 뉴스 해석 + prebuilt semantic knowledge 조회 + relevance-driven
소수 raw citation 확인 + 최종 합성만 수행한다.

FORBIDDEN:
raw corpus, 모든 material cluster-record 관계, 모든 lane assignment에
비례하는 daily LLM fan-out을 만들지 않는다.

QUALITY_FULL:
90초 초과만으로 유효한 호출을 중단하지 않는다는 뜻이며,
무제한 daily call graph 또는 운영성 무시를 뜻하지 않는다.

EVALUATION:
CALIBRATION·HOLDOUT은 실제 배포할 daily path와 동일한 architecture를
blind하게 평가하며, one-time 비용과 per-day 비용을 분리 보고한다.

CONFLICT:
이 계약과 충돌하는 기존 문서·외부 피드백·goal prompt는 실행하지 않고
사용자에게 충돌을 먼저 보고한다.
```

이 머리말의 의미를 축소하거나 evaluator 편의를 위해 daily path를 별도
exhaustive architecture로 바꾸면 해당 goal은 무효다.

## 중단된 379-Pack 경로

`QPRED-704f15cde6e4152b6931` / `RUN-9701018d4a4e`는 blind boundary 자체는
지켰지만 제품 의도와 다른 architecture를 평가했다. 2026-01-02 하루에
49,984개 관계를 379개 대형 prompt로 다시 해석하도록 설계돼 장전 운영에
사용할 수 없다.

이 ancestry는 `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`다. 완료된 5개 pack과 plan은
forensics 목적으로 보존하지만, 재개·채점·비교·승격·formal cache 재사용을
금지한다.
