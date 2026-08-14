# Phase 8 shadow replay·부하·편향 평가 보고서

상태: bounded evaluator와 source-closure verifier 구현 및 외부 독립감사 완료, 실제 corpus production gate 차단

## 1. 구현 범위

Phase 8은 동일한 사전 등록 날짜와 complete outcome universe를 사용하는 A~F 비교를 strict artifact로
계산한다.

```text
A  memory 없음
B  frozen historical legacy top-3
C  memory cells + population statistics
D  memory cells + representatives
E  memory cells + population + representatives
F  E + adaptive drill-down
```

arm 이름만 바꾸는 것을 막기 위해 feature tuple과 snapshot kind를 contract가 exact 검증한다. A는 memory
record를 가질 수 없고, B는 `LEGACY_TOP3_INDEX`, C~F는 동일한 `PRODUCTION_MEMORY_CELLS` snapshot만
허용한다. 같은 날짜의 A~F는 runner protocol, LLM provider/model, prompt, inference config를 공유해야 한다.
B~F는 corpus root, source generation, cutoff, llm-full brain snapshot을 공유해야 한다.
Historical production closure는 문자열 attestation만 신뢰하지 않고 factory-issued OpenAI provider 객체와
validated stock-web price source를 요구한다. 구조만 맞춘 fake protocol 객체는 거부한다.

## 2. split과 lookahead 경계

build, calibration, holdout 범위는 서로 겹칠 수 없고 calibration/holdout 예정 날짜 목록을 exact하게
사전 등록한다. `memory seal-shadow-split`은 calibration 시작 전 실제 명령 실행 시각을 `sealed_at`으로 고정하고
`NSLAB_SHADOW_EVALUATION_HMAC_KEY`로 날짜 계획과 issued time을 HMAC-SHA256 서명한다. 최소 key 길이는
32 UTF-8 bytes다. public seal API는 호출자가 과거 `issued_at`을 주입할 수 없다.

`memory seal-shadow-dataset`은 split, case, A~F observation, truth, telemetry, load profile 전체 closure를 별도
HMAC commitment로 봉인한다. split과 dataset은 commitment로 결정되는 canonical 경로에서만 평가할 수 있다.
각 arm observation 전체 payload도 observation hash로 결정되는
`runs/shadow_evaluation/arm_observations/<case>/<arm>/<sha>.json` receipt에 보존한다.

dataset sealer가 telemetry나 qualitative truth를 사후 작성하지 못하도록 source authority를 분리한다.
`seal_shadow_arm_observation`은 `NSLAB_SHADOW_RUNNER_HMAC_KEY`로 실제 실행 종료 5분 안에 full arm/telemetry를
서명한다. `seal_shadow_case_truth`는 `NSLAB_SHADOW_TRUTH_HMAC_KEY`로 cutoff 이후 complete outcome과
retrieval/theme/leader/newsless truth를 별도 서명한다. dataset sealer와 inspector는 두 attestation을
재계산·검증하며, telemetry/truth를 새 content-addressed 경로로 옮겨도 원 서명과 다르면 거부한다.
같은 runner key를 쓰는 `seal_shadow_load_profile`은 workload, 모든 sample receipt, raw arrays, summary,
snapshot identity를 aggregate 서명하며 마지막 sample 완료 5분 이내에만 발급한다.

평가 시 다음을 fail-close한다.

```text
HMAC key/signature/commitment 불일치
사전 등록 날짜 누락 또는 추가
incomplete outcome universe
날짜별 A~F multiplicity 불일치
candidate가 truth universe 밖에 존재
record available_from 또는 trade_date가 replay cutoff 이후
C~F snapshot canonical path/MemoryCellSnapshotManifest v3/deep inspection 실패
B frozen top-3 index ID·순서·hash/source generation 불일치
B~F brain/corpus/source generation/cutoff 불일치
C~F retrieved record와 snapshot DuckDB projection 불일치
historical news CSV cutoff-after row
모든 날짜·arm source file hash/count 불일치
blind prediction/context manifest 후보·retrieved ID·ablation config 불일치
historical context의 configured provider/model/provider class attestation 불일치
runner arm attestation 또는 independent truth attestation 불일치
전체 run manifest lookahead audit 실패 또는 검사 수 부족
live full-market price universe와 truth projection 불일치
FULL_MARKET_COMPLETE postmortem 부재 또는 truth hash/retrieval label 봉인 불일치
BlindPrediction candidate의 claimed theme/news-cause projection 불일치
postmortem ticker별 theme/leader/newsless truth projection 불일치
B top-3 record와 C~F production snapshot DB projection 불일치
```

## 3. 지표

판단 지표:

```text
candidate Recall@5/10/20
high +10/+20 recall
false-positive rate
leader selection error
theme over-expansion
newsless cause hallucination
Brier score / expected calibration error
```

confidence probability는 임의 상수로 매핑하지 않는다. calibration 기간의 arm별 confidence bucket empirical
rate를 고정한 뒤 holdout에만 적용한다. Brier는 선택 후보만이 아니라 complete outcome universe 전체를
분모로 사용하며 미선택 ticker는 확률 0으로 평가한다. holdout candidate의 confidence bucket이 calibration에
없으면 Brier는 `None`이고 exit gate는 닫힌다.

retrieval 지표:

```text
known relevant record recall
negative-control inclusion
counterexample inclusion
long-tail beneficiary recall
independent-unit duplicate rate
year/regime diversity
```

시스템 지표:

```text
pre-LLM 및 daily P50/P95/P99
LLM input/output tokens
embedding query count
cache hit rate
peak memory
estimated cost
online full scan count
```

## 4. artifact와 재계산

`memory evaluate-shadow <dataset>`은 다음 content-addressed artifact를 쓴다.

```text
runs/shadow_evaluation/<evaluation_id>/source_dataset.json
runs/shadow_evaluation/<evaluation_id>/case_results.jsonl
runs/shadow_evaluation/<evaluation_id>/calibration_buckets.jsonl
runs/shadow_evaluation/<evaluation_id>/shadow_evaluation_manifest.json
```

evaluation ID는 dataset bytes와 protocol/metric/calibration/bias/system-budget version으로 결정한다.
`memory inspect-shadow`는 source artifact hash/count/path, split/truth/arm projection, snapshot deep audit, case rows,
calibration bucket과 aggregate manifest를 모두 재계산한다. 결과 JSONL과 manifest hash를 함께 바꾸는 coherent
tamper도 source에서 다시 계산한 bytes와 다르면 거부한다.

## 5. exit gate

production ready가 되려면 모두 만족해야 한다.

```text
calibration 20일 이상
holdout 20일 이상
동일한 E 또는 F arm이 B보다 Recall@20과 full-universe Brier를 함께 개선
newsless hallucination 악화 없음
E/F pre-LLM P95 <= 5초
E/F daily P95 <= 90초
normal input P95 <= 50k tokens, hard max <= 80k
online full scan 0
50k/200k/600k production-shaped real 1536D profile 통과
selection/survivorship audit 통과
실제 analyzer prediction/context/lookahead/price source closure 독립 검증
```

load profile의 percentile과 peak는 self-declared summary가 아니다. 최소 5개 canonical raw sample receipt와
고유 run ID·시작/종료 시각·content-addressed workload artifact를 contract가 재계산한다. workload는 snapshot,
provider/model/dimension, operation, sample count를 exact 봉인한다. receipt별 latency/memory/scan 값과 profile
배열은 exact 일치해야 한다. 각 50k/200k/600k profile은 같은 record count의 canonical production memory
snapshot, real provider/model/dimension, corpus/source generation, deep inspection과 exact 결속한다. unmeasured
profile은 blocker reason을 반드시 남기며 production ready가 될 수 없다.
모든 sample 완료와 load attestation은 dataset `created_at` 이하여야 한다.

## 6. 합성 회귀 결과

20 calibration + 20 holdout 날짜, 각 날짜 A~F exact arm을 가진 deterministic fixture 결과:

```text
B candidate Recall@20 = 0.0
E/F candidate Recall@20 = 1.0
B full-universe Brier = 0.75
E/F full-universe Brier = 0.0
E known relevant / negative-control / counterexample inclusion = 1.0
metric/budget checks = PASS
actual A~F source closure = BLOCKED
synthetic production_ready = false
production-shaped historical closure fixture = PASS
```

이 결과는 알고리즘·계약 회귀이며 실제 투자 성능 증거가 아니다.

## 7. 실제 corpus read-only 결과

`memory shadow-readiness` 최신 결과:

```text
prediction dates                   1
postmortem dates                  1
paired historical dates           1 (2026-06-24)
production memory snapshot        missing
current brain mode                catalog
real LLM/provider                 false
real price provider               false
real web provider                 false
shadow pre-registration key       false
shadow runner attestation key     false
shadow truth attestation key      false
actual verified A~F evaluation    absent
ready                             false
```

따라서 현재 실제 corpus로 A~F recall/calibration 개선을 주장하지 않는다. Phase 9 전 repaired corpus import와
production pointer도 변경하지 않았다.

## 8. 부하 실측

최신 reduced-schema DuckDB query microbenchmark는 32D/1,024 synthetic cells이며 production exit gate가
아니다.

```text
records   ANN median   FTS median   member median   DB bytes
50k       0.871 ms     87.100 ms    1.354 ms        5,517,312
200k      1.193 ms     217.558 ms   2.192 ms        15,216,640
600k      1.035 ms     332.285 ms   2.720 ms        40,382,464
```

모든 크기에서 HNSW plan 확인, future member 0, query microbenchmark PASS다. 다만 real 1536D embedding API,
production sidecar/index build, population/representative/daily integration, peak RSS를 포함하지 않으므로
50k/200k/600k production load profile은 여전히 미실측이다.

## 9. 테스트와 남은 blocker

추가 회귀:

```text
A~F 정상 평가와 deterministic self-inspection
unmeasured load blocker 보존
raw sample과 percentile 불일치 거부
네 telemetry sample 배열/receipt count 불일치 거부
case result + manifest hash coherent tamper 거부
snapshot manifest drift 거부
production snapshot deep inspection 실패 거부
B top-3와 production snapshot DuckDB record projection parity
wrong split HMAC key 거부
declared holdout 날짜 누락 거부
split seal content addressing
dataset full-closure seal과 canonical path 강제
C~F snapshot 및 A~F 실행 설정 parity
synthetic/self-declared historical production 승격 차단
historical prediction/context/news/postmortem/price/lookahead source closure
전체 날짜·arm source hash와 production provider/model/class exact binding
postmortem truth hash와 retrieval ground-truth label binding
BlindPrediction claimed theme/news-cause와 postmortem theme/leader/newsless exact binding
B~F brain/corpus 및 C~F DB record projection parity
retrieved ticker와 DB-verified independent-unit issuer projection parity
load execution receipt와 50k/200k/600k production snapshot deep binding
load workload artifact coherent rewrite 거부
structural fake LLM/price provider production 승격 거부
provider subclass override production 승격 거부
coherent telemetry receipt rewrite와 coherent qualitative-truth/postmortem rewrite 거부
coherent workload/sample/profile rewrite와 future-dated load receipt 거부
CLI seal/evaluate/inspect/readiness
```

최종 고정 tree 독립감사 결과는 `APPROVE`이며 P0/P1은 0건이다. 외부 감사자가 provider subclass,
잘못된 runner/truth/load key, telemetry·truth·load의 coherent rewrite/relocation, 미래 시각 sample·attestation,
public sealer backdating을 다시 공격해 모두 거부됨을 확인했다.

```text
focused scaffold + shadow     40 passed
full pytest                    1,457 passed (325.68s)
ruff                           PASS
mypy                           108 source files PASS
schema regeneration/parity     41 schemas, mismatch 0
git diff --check               error 0 (line-ending warning only)
```

남은 production blocker:

```text
paired historical date 40일 미만
actual A~F arm run artifact 없음
current corpus production memory snapshot 없음
llm-full production brain 없음
real provider 미설정
runner/truth attestation key 미설정
50k/200k/600k real 1536D end-to-end profile 미실측
current corpus unsupported REASONING repair 미완료
```

이 blocker가 남아 있는 동안 Phase 8 code contract는 사용할 수 있지만 Phase 9 production enable 조건은
충족되지 않는다.
