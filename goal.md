당신은 이 저장소의 수석 아키텍트이자 구현 담당자다.

이번 작업은 단순 설계안 작성이 아니다. 계획 수립부터 실제 코드, 스키마, CLI, LLM 실행기, 연구 메모리 컴파일러, 테스트, 문서까지 동작 가능한 형태로 한 번에 구현하라.

질문이 없어도 합리적인 기본값으로 끝까지 진행하라.
외부 API 키가 없어 실호출을 할 수 없는 부분은 provider interface와 deterministic mock으로 완성하고, API 키를 넣으면 즉시 실제 실행되게 만들어라.
중간 계획만 제출하고 멈추지 마라.
모든 테스트를 실행하고 실패를 수정한 뒤 최종 결과를 제출하라.

# 0. 프로젝트의 본질

프로젝트 이름은 임시로 `news-scalping-lab`으로 한다.

이 프로젝트의 목표는 다음과 같다.

1. 사용자는 GPT Web Pro 연구 세션에서 과거 날짜별 연구를 수행한다.
2. 각 연구에는 전 거래일 15:30부터 해당 거래일 08:59:59 KST까지의 뉴스 CSV와, 가격 결과 확인을 위한 stock-web 데이터가 사용된다.
3. 연구 결과는 몇 년 동안 날짜별로 저장된다.
4. 저장된 연구 결과는 코드의 if/else, 키워드 사전, 종목 화이트리스트, 고정 점수표로 변환되지 않는다.
5. 대신 모든 연구 결과가 LLM이 매번 읽고 사고할 수 있는 지속형 연구 두뇌로 편입된다.
6. 새 아침 뉴스 CSV를 넣으면, 연구 두뇌를 가진 LLM 에이전트가:
   - 추가 웹 검색을 수행하고
   - 뉴스 사건을 해석하고
   - 과거 연구에서 배운 사고방식을 사용하고
   - 처음 보는 사건도 오픈월드 방식으로 추론하며
   - 주도섹터
   - 직접 단일뉴스 상한가 후보
   - 정책·산업 뉴스에서 파생되는 수혜주 상한가 후보
   - 전일 대장 연속성 후보
   - 장전 관심종목
   을 출력한다.
7. 새 섹터, 새 지역, 새 정책, 새 회사가 등장해도 production 코드를 고칠 필요가 없어야 한다.
8. 연구자료에 정확히 같은 단어가 없더라도 현재 사건의 작동 원리를 추론할 수 있어야 한다.
9. 과거 연구 검색 결과가 없다는 이유로 후보를 탈락시키지 않아야 한다.
10. 결과는 자동매매 명령이 아니라 장전 관심종목 연구 결과다.

이 프로그램은 `뉴스 → 하드코딩 규칙 → 종목` 엔진이 아니다.

이 프로그램은:

`뉴스 → 장기 연구기억을 가진 LLM의 오픈월드 분석 → 웹 조사 → 과거 성공·실패 사례 검토 → 후보 생성 및 비교`

구조여야 한다.

# 1. 가장 중요한 절대 금지사항

아래 항목은 어떠한 형태로도 production 코드에 구현하지 마라.

## 1.1 도메인 키워드 하드코딩 금지

다음과 같은 코드를 절대 만들지 마라.

```python
if "호남" in title:
    sectors = ["건설", "레미콘"]
    tickers = ["000890", ...]
````

```python
THEME_MAP = {
    "양자컴퓨터": ["엑스게이트", ...],
    "반도체 공장": ["건설", "전력", ...],
}
```

```python
if event_type == "국책과제":
    score += 5
if "세계 최초" in title:
    score += 10
```

금지 범위:

* 정책명 → 섹터 고정 매핑
* 섹터 → 종목 고정 매핑
* 지역명 → 지역주 고정 매핑
* 뉴스 표현 → 고정 점수
* 실제 종목명·티커를 src 코드에 박기
* 과거 상한가 종목을 허용 목록으로 사용하기
* 특정 연구자료가 검색되지 않으면 후보를 제거하기
* `MOU=낮음`, `계약=높음` 같은 단순 고정 테이블을 최종 판단기로 사용하기
* exact keyword 검색만으로 과거 사례를 찾기
* LLM이 추론하기 전에 코드가 후보군을 좁히기
* 연구결과를 새 if/else 코드로 번역하기

날짜 파싱, URL 파싱, CSV 컬럼 처리, 종목코드 형식 검증처럼 의미 판단이 아닌 기술적 파싱은 허용한다.

## 1.2 연구자료 검색이 판단의 문지기가 되어서는 안 됨

잘못된 구조:

```text
현재 뉴스에서 키워드 추출
→ 같은 키워드 연구자료 검색
→ 없으면 관련 없음
→ 후보 제거
```

올바른 구조:

```text
현재 뉴스만 보고 오픈월드 작동 원리 먼저 추론
→ 가능한 직접 종목·섹터·수혜경로를 자유롭게 생성
→ 과거 연구를 다양한 의미·인과·구조 관점으로 조회
→ 성공사례와 실패사례로 최초 추론을 확장·반박
→ 과거 사례가 없어도 현재 증거로 새 후보 생성 가능
```

## 1.3 AGENTS.md에 연구지식 저장 금지

`AGENTS.md`에는 다음만 기록한다.

* 프로젝트 목적
* 하드코딩 금지
* 미래정보 누수 금지
* 테스트 명령
* 데이터 불변성
* 구현 관례
* 완료 기준

종목별 기억, 테마 지식, 과거 연구결론은 `AGENTS.md`나 source code가 아니라 `research/`, `memory/`, `brain/`에 저장한다.

## 1.4 결과를 본 뒤 만든 설명을 블라인드 기억으로 저장 금지

모든 날짜는 반드시 다음 두 단계로 분리한다.

```text
BLIND
가격 결과를 보기 전 사건 분석과 후보 예측

POSTMORTEM
가격 결과 공개 후 적중·누락·오탐 연구
```

두 단계의 파일과 데이터 모델을 분리하고, 블라인드 예측을 먼저 해시로 봉인한 후에만 결과를 붙일 수 있게 한다.

# 2. 이 프로젝트가 세션처럼 작동해야 하는 방식

몇 년치 원본 연구를 한 번의 모델 호출에 전부 넣는 방식만 고집하지 않는다. 대신 아래 세 층을 구현해서 장기 세션과 같은 효과를 재현한다.

## 2.1 항상 로드되는 통합 연구 두뇌

모든 과거 연구를 LLM이 종합하여 만든 최신 버전형 연구 두뇌다.

최소 구성:

```text
brain/current/
├─ 00_world_model.md
├─ 01_single_event_patterns.md
├─ 02_theme_formation_patterns.md
├─ 03_beneficiary_discovery.md
├─ 04_leader_selection.md
├─ 05_continuation_patterns.md
├─ 06_failure_modes.md
├─ 07_counterexamples.md
├─ 08_market_memory.md
├─ claims.jsonl
├─ coverage_manifest.json
└─ brain_manifest.json
```

이 파일들은 단순 키워드 사전이 아니다.

저장해야 하는 것은 다음과 같은 추상적인 작동 원리다.

```text
대규모 지역 산업투자
→ 생산시설 건설
→ 전력·용수·물류·건설 수요
→ 실제 사업 수혜 후보
→ 지역자산·과거 관련주 기억에 의한 내러티브 후보
→ 전일 선행수급이 붙은 후보가 대장으로 선택될 가능성
```

다음처럼 단순히 외우면 안 된다.

```text
호남 → 보해양조
```

모든 원리에는 반드시 근거 episode ID, 지지 사례, 반례, 적용 조건, 실패 조건을 붙인다.

## 2.2 모든 연구가 빠짐없이 반영되는 전수 메모리 스윕

단순 벡터 검색만 사용하면 과거 연구가 검색되지 않아 사라지는 문제가 생긴다.

따라서 daily 분석에 다음 세 모드를 구현한다.

### exhaustive 모드

연구 품질 검증 및 최종 고품질 실행용 기본 모드다.

1. 현재 뉴스에 대해 과거 연구 없이 최초 오픈월드 분석을 수행한다.
2. 전체 accepted 연구 episode를 토큰 예산에 맞춰 shard로 나눈다.
3. 각 shard에 현재 뉴스와 최초 분석을 함께 제공한다.
4. 각 shard LLM이 다음을 반환한다.

   * 관련될 수 있는 과거 교훈
   * 성공 유사사례
   * 실패 유사사례
   * 구조는 비슷하지만 다른 사례
   * 현재 최초 추론에 대한 반박
   * 새 후보 및 새 수혜경로
5. 모든 shard 결과를 최종 synthesizer에 전달한다.
6. synthesizer가 항상 로드된 global brain과 함께 최종 결과를 만든다.

핵심 요구사항:

> exhaustive 모드에서는 accepted 연구 episode 100%가 최소 한 번의 LLM 분석 컨텍스트에 포함되어야 한다.

exact keyword나 semantic retrieval hit 여부와 무관해야 한다.

### brain 모드

다음 모두를 로드한다.

* 최신 global brain 전체
* 모든 shard brain 요약
* 현재 사건과 관련성이 높은 원본 episode
* 반례 episode
* 최근 시장기억

모든 episode는 shard brain을 통해 간접적으로라도 반드시 영향을 줘야 한다.

### fast 모드

* global brain
* 의미 검색된 사례
* 반례 검색
* 시장기억

만 사용한다.

단, fast 모드도 retrieval miss를 후보 탈락 사유로 사용하면 안 된다.

CLI 기본값은 초기에는 `exhaustive`로 설정하라.
비용을 줄일 필요가 있을 때 사용자가 명시적으로 `brain` 또는 `fast`를 선택하게 한다.

## 2.3 필요할 때 원문까지 들어가는 동적 기억

현재 사건을 분석한 LLM이 직접 여러 검색 질의를 생성한다.

검색 질의는 단어 일치가 아니라 다음 관점을 포함해야 한다.

* 인과 메커니즘이 유사한 사례
* 시장 내러티브 확산 방식이 유사한 사례
* 직접 회사뉴스와 정책 파생뉴스의 차이
* 성공 사례
* 실패 사례
* 후보에는 있었지만 대장이 되지 못한 사례
* 겉보기에는 비슷하지만 결과가 반대였던 사례
* 처음 보는 정책에서 시장이 예상 밖의 종목을 선택한 사례

검색은 판단 보조 수단이며 후보 허용 목록이 아니다.

# 3. 기술 스택

기본 구현은 Python 3.12 이상으로 한다.

권장 구성:

```text
Python 3.12+
Typer
Pydantic v2
DuckDB
PyArrow / Parquet
SQLite FTS5
LanceDB 또는 동등한 로컬 벡터 저장소
OpenAI Python SDK
httpx
tenacity
structlog
Jinja2
pytest
pytest-asyncio
ruff
mypy
```

구현 원칙:

* 외부 LLM provider는 interface로 추상화한다.
* 기본 provider는 OpenAI Responses 기반으로 구현한다.
* 테스트용 `DeterministicMockLLMProvider`를 제공한다.
* embeddings provider도 분리한다.
* web research provider도 분리한다.
* 모든 비밀키는 `.env`에서 읽는다.
* 비밀키를 저장소에 저장하지 않는다.
* LLM 모델명, reasoning 설정, 토큰 예산, 동시성은 config에서 변경 가능해야 한다.
* 특정 모델명에 비즈니스 로직을 의존시키지 않는다.

# 4. 저장소 구조

아래 구조를 기본으로 실제 구현하라.

```text
news-scalping-lab/
├─ AGENTS.md
├─ PLANS.md
├─ README.md
├─ pyproject.toml
├─ .env.example
├─ .gitignore
├─ Makefile
├─ configs/
│  ├─ default.yaml
│  ├─ models.yaml
│  ├─ context_budget.yaml
│  ├─ inference.yaml
│  └─ evaluation.yaml
├─ schemas/
│  ├─ research_episode.schema.json
│  ├─ blind_prediction.schema.json
│  ├─ postmortem.schema.json
│  ├─ memory_claim.schema.json
│  ├─ event_ticker_edge.schema.json
│  ├─ brain_manifest.schema.json
│  └─ daily_analysis.schema.json
├─ prompts/
│  ├─ research_import/
│  ├─ brain_compile/
│  ├─ blind_analysis/
│  ├─ memory_sweep/
│  ├─ web_research/
│  ├─ candidate_generation/
│  ├─ red_team/
│  ├─ synthesis/
│  └─ evaluation/
├─ data/
│  ├─ inbox/
│  │  ├─ news/
│  │  └─ research/
│  ├─ raw/
│  │  ├─ news/
│  │  └─ research/
│  ├─ normalized/
│  ├─ quarantine/
│  └─ cache/
├─ research/
│  ├─ episodes/
│  ├─ accepted/
│  ├─ rejected/
│  ├─ hypotheses/
│  ├─ counterexamples/
│  └─ indexes/
├─ memory/
│  ├─ episodes/
│  ├─ claims/
│  ├─ mechanisms/
│  ├─ event_ticker_edges/
│  ├─ market_memory/
│  ├─ company_memory/
│  ├─ shard_brains/
│  └─ vector_index/
├─ brain/
│  ├─ snapshots/
│  ├─ current/
│  ├─ diffs/
│  └─ HEAD
├─ warehouse/
│  ├─ events.parquet
│  ├─ event_sources.parquet
│  ├─ event_ticker_edges.parquet
│  ├─ research_episodes.parquet
│  ├─ daily_outcomes.parquet
│  ├─ predictions.parquet
│  └─ market_memory.parquet
├─ predictions/
├─ reports/
├─ runs/
│  ├─ manifests/
│  ├─ traces/
│  └─ checkpoints/
├─ src/
│  └─ news_scalping_lab/
│     ├─ __init__.py
│     ├─ cli.py
│     ├─ config.py
│     ├─ contracts/
│     ├─ ingest/
│     ├─ research_import/
│     ├─ brain/
│     ├─ context/
│     ├─ llm/
│     ├─ agents/
│     ├─ tools/
│     ├─ web/
│     ├─ prices/
│     ├─ outcomes/
│     ├─ memory/
│     ├─ retrieval/
│     ├─ inference/
│     ├─ evaluation/
│     ├─ audits/
│     └─ reporting/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ metamorphic/
│  ├─ fixtures/
│  └─ live/
└─ .agents/
   └─ skills/
      └─ news-scalping-lab/
         ├─ SKILL.md
         ├─ references/
         └─ scripts/
```

# 5. 연구 episode 데이터 계약

GPT Web Pro 연구 결과를 장기 기억으로 편입하기 위한 canonical schema를 구현한다.

최소 모델:

```python
ResearchEpisode:
    episode_id
    trade_date
    cutoff_at
    created_at
    research_version
    input_news_files
    input_news_hashes
    price_source_snapshot
    blind_analysis
    blind_predictions
    outcome_labels
    postmortem
    observed_events
    event_ticker_edges
    lessons
    counterexamples
    misses
    provenance
    available_from
```

`blind_analysis`와 `postmortem`은 별도 객체로 분리한다.

`available_from`은 해당 연구 교훈이 언제부터 이후 예측에 사용될 수 있는지를 나타낸다.

예:

```text
2024-06-10 장의 결과를 본 postmortem 교훈
→ 2024-06-11 이후 분석에서만 사용 가능
```

워크포워드 역사 연구에서 미래 교훈이 과거 예측에 들어가지 않게 한다.

## 5.1 임의 형식 연구자료도 가져올 수 있게 할 것

초기 연구 결과가 완전한 JSON이 아니라 MD일 수 있다.

따라서 import는 두 경로를 제공한다.

```text
strict import
canonical JSON을 schema validation 후 수입

semantic import
MD 또는 혼합 산출물을 LLM이 읽고 canonical ResearchEpisode로 변환
```

semantic import에서 regex로 의미를 해석하지 마라.
LLM structured output을 사용한다.

원본은 절대 수정하지 않고 content hash와 함께 보존한다.

변환 결과에는 모든 문장이 어느 원본 파일에서 왔는지 provenance를 저장한다.

# 6. 연구 두뇌의 데이터 모델

## 6.1 MemoryClaim

```python
MemoryClaim:
    claim_id
    statement
    mechanism
    scope
    conditions
    failure_modes
    support_episode_ids
    contradiction_episode_ids
    near_miss_episode_ids
    status
    confidence_label
    first_observed_at
    last_updated_at
    available_from
    provenance
```

고정 숫자 점수로 시장법칙을 만들지 마라.

상태 예:

```text
tentative
supported
validated
disputed
retired
```

서로 모순되는 claim은 하나를 삭제하지 말고 둘 다 유지하고, 어떤 조건에서 갈리는지 연구하게 한다.

## 6.2 EventTickerEdge

```python
EventTickerEdge:
    edge_id
    episode_id
    event_id
    ticker
    company_name
    relation_class
    relation_explanation
    directly_mentioned
    fundamental_evidence
    narrative_evidence
    market_memory_evidence
    temporal_validity
    confidence_label
    provenance
```

relation_class는 최소한 다음과 같은 상위 개념을 지원하되, 산업별 고정 mapping은 만들지 않는다.

```text
DIRECT
FUNDAMENTAL
MARKET_MEMORY
CONTINUATION
INFERRED_NEW
```

## 6.3 MechanismMemory

특정 단어가 아니라 사건의 작동 원리를 저장한다.

```python
MechanismMemory:
    mechanism_id
    natural_language_description
    causal_chain
    observed_variations
    successful_cases
    failed_cases
    boundary_conditions
    leader_selection_notes
    provenance
```

## 6.4 CompanyMemory

회사별 시장 기억은 코드가 아니라 데이터로 저장한다.

```python
CompanyMemory:
    ticker
    company_name
    aliases
    business_descriptions
    locations
    customers
    supply_chain_roles
    prior_market_narratives
    prior_leader_occurrences
    contradictory_relations
    known_at
    provenance
```

이 메모리는 과거 연구와 웹 조사로 갱신한다.
새 회사는 LLM과 웹 도구가 발견할 수 있어야 한다.
기존 company memory에 없다는 이유로 후보를 제거하지 않는다.

# 7. Brain Compiler

`brain rebuild` 명령을 구현한다.

```bash
nslab brain rebuild --mode full
```

기능:

1. 모든 accepted episode를 읽는다.
2. 각 episode의 원본, blind, outcome, postmortem을 구분한다.
3. 시간 순서를 보존한다.
4. LLM이 episode별 기억 단위를 작성한다.
5. 일정 토큰 단위로 shard brain을 만든다.
6. shard brain들을 다시 상위 수준에서 종합한다.
7. global brain을 생성한다.
8. 기존 brain과의 diff를 생성한다.
9. coverage manifest를 생성한다.
10. 모든 accepted episode가 반영됐는지 검증한다.
11. support와 contradiction을 함께 보존한다.
12. 새 brain snapshot을 immutable하게 저장한다.
13. `brain/HEAD`를 새 버전으로 변경한다.

Brain compiler가 해서는 안 되는 것:

* 다수결만 보고 소수 반례 삭제
* 최근 사례만 남기기
* 종목별 외운 목록 생성
* exact keyword index를 세계관으로 사용
* 기존 결론과 충돌하는 연구를 버리기
* 모든 연구를 하나의 점수표로 환원하기

## 7.1 점진 갱신

```bash
nslab brain update --episode <episode_id>
```

새 episode만 반영하는 incremental update도 제공한다.

다만 drift 방지를 위해:

```bash
nslab brain rebuild --mode full
```

로 전체 원본에서 언제든 재생성 가능해야 한다.

## 7.2 Coverage audit

```bash
nslab brain audit
```

출력:

```text
accepted episode 수
brain에 반영된 episode 수
누락 episode
출처 없는 claim
support가 하나뿐인 과도한 일반화
반례가 누락된 claim
시간 누수 가능성
마지막 full rebuild 시각
brain version
```

coverage는 100%가 아니면 실패로 처리한다.

# 8. 가격 저장소 연결

기본 가격 소스:

```text
primary_price_source_url =
https://github.com/Songdaiki/stock-web
```

`StockWebPriceSource` adapter를 구현한다.

로컬 clone 경로와 원격 GitHub cache 방식을 둘 다 지원한다.

기본 데이터 컬럼 후보:

```text
d  date
o  open
h  high
l  low
c  close
v  volume
a  amount
mc market_cap
s  listed_shares
m  market
```

실제 repository schema를 읽고 adapter를 맞춰라.
컬럼 이름과 파일 경로를 무작정 가정하지 말고 manifest/schema를 검사하라.

## 8.1 블라인드 가격 접근 규칙

거래일 D를 예측할 때 inference agent가 사용할 수 있는 가격은 최대 D-1이다.

다음은 금지한다.

```text
D의 시가
D의 고가
D의 저가
D의 종가
D의 거래량
D의 거래대금
D의 상한가 여부
```

`BlindPriceGuard`를 구현해 D 데이터를 요청하면 예외를 발생시켜라.

## 8.2 결과 라벨

평가 단계에서만 D 가격을 읽는다.

최소 라벨:

```text
open_gap_pct
intraday_high_return_pct
close_return_pct
upper_limit_touched
upper_limit_closed
upper_limit_released
one_price_upper_limit
volume
amount
turnover_ratio
market_cap_previous_close
```

신규상장일, 기업행위 의심일, 기준가격 불확실일은 별도 flag를 붙이고 일반 상한가 라벨에 억지로 포함하지 않는다.

분봉 데이터가 없으면 다음을 추정하지 마라.

```text
09시 첫 1분봉
상한가 최초 도달 시각
VI 횟수
첫 3분 수익률
```

일봉으로 알 수 없는 것은 명시적으로 `unavailable`로 남긴다.

# 9. 시간 안전장치

모든 실행에는 명시적 `as_of`와 `trade_date`가 있어야 한다.

기본 뉴스 범위:

```text
전 거래일 15:30:00 KST
~
당일 08:59:59 KST
```

사용자가 CSV를 직접 제공하더라도:

* 게시시각
* 수집시각
* 뉴스 범위
* trade date
* cutoff
  를 검증한다.

## 9.1 웹 검색 시간 제한

역사적 날짜를 분석할 때 웹 검색 결과가 cutoff 이후 발행된 자료면 blind evidence로 사용하지 않는다.

각 검색 결과에:

```text
published_at
time_verified
retrieved_at
source_url
```

을 저장한다.

게시시각을 확인할 수 없으면:

```text
time_verified = false
```

로 처리하고 블라인드 확정 근거로 사용하지 않는다.

현재 날짜 분석에서도 `as_of` 이후 자료를 사용하지 않는다.

# 10. Daily LLM Analyst

다음 명령을 구현한다.

```bash
nslab analyze \
  --news data/inbox/news/0700_news_YYYYMMDD.csv \
  --trade-date YYYY-MM-DD \
  --cutoff YYYY-MM-DDT08:59:59+09:00 \
  --mode exhaustive \
  --web-search
```

분석 순서는 반드시 다음과 같다.

## Pass 0: Open-world first read

과거 연구 검색 전에 현재 뉴스만 읽고 LLM이 다음을 작성한다.

```text
사건 군집
직접 회사 사건
정책·산업 사건
작동 메커니즘
수혜가 전파될 수 있는 경로
시장 내러티브로 변환될 수 있는 지점
직접 후보
잠재 섹터
잠재 수혜주를 찾기 위한 조사 질문
불확실성
```

이 단계에서는 과거 연구에 후보를 맞추지 않는다.

## Pass 1: 뉴스 중복 군집화 및 신규성 조사

같은 사건의 반복기사를 하나로 묶는다.

LLM과 웹 도구를 사용해:

* 최초 공개 시각
* 장후 신규 공시 여부
* 기존 뉴스 재탕 여부
* 본계약인지 예정인지
* 회사 귀속 금액
* 고객
* 기간
* 승인 단계
* 희석·유증·CB 등 반대 요인
  을 조사한다.

의미 판단은 LLM이 수행한다.
코드는 결과의 스키마와 시간제약만 검증한다.

## Pass 2: 전수 연구 메모리 스윕

exhaustive mode에서는 모든 accepted episode shard에 현재 사건을 제공한다.

각 shard analyzer 출력:

```text
현재 사건과 구조적으로 유사한 교훈
성공 유사사례
실패 유사사례
현재 최초 추론을 지지하는 내용
현재 최초 추론을 반박하는 내용
새롭게 생각할 수 있는 섹터
새롭게 조사할 종목
직접성보다 시장기억이 중요한 사례
같은 뉴스에서 대장이 되지 못한 후보의 이유
```

검색 결과가 0건이어도 오류가 아니다.

## Pass 3: 추가 의미검색

LLM이 생성한 여러 query로 원본 episode를 추가 조회한다.

반드시 다음 종류를 함께 가져온다.

```text
positive analogs
negative analogs
near misses
counterexamples
leader-selection cases
theme-formation failures
```

## Pass 4: 오픈월드 후보 확장

다음 네 경로를 독립적으로 실행한다.

### A. SINGLE_EVENT

기사·공시에서 직접 언급된 회사의 단일 사건 후보.

### B. THEME_FORMATION

정책·산업·지역·글로벌 사건이 주도섹터를 만들 가능성.

### C. BENEFICIARY_DISCOVERY

테마 형성 시 직접 수혜, 공급망, 인프라, 지역 자산, 시장 기억 등으로 파생되는 종목.

여기서 후보는 기존 연구에 등록된 종목만 허용하지 않는다.
LLM이 웹 검색과 회사 조사로 새로운 후보를 생성할 수 있어야 한다.

### D. CONTINUATION

전일 또는 최근 시장이 이미 선택한 대장과 수급의 연속성.

이 분석에는 D-1까지의 가격만 사용한다.

## Pass 5: 후보별 웹 검증

후보마다 최소한 다음을 검증한다.

```text
상장 종목인지
정확한 티커인지
실제 사업·위치·고객·공급망 관계
과거 어떤 테마로 거래됐는지
현재 뉴스와 관계가 실제인지 단순 이름 유사성인지
최근 공시 및 뉴스
시총
상장주식수
직전 거래일 거래대금·회전율·상한가 여부
이미 며칠간 선반영됐는지
```

후보가 과거 research memory에 없더라도 웹 조사로 새롭게 포함할 수 있어야 한다.

## Pass 6: Red-team

별도 LLM pass로 다음을 공격적으로 검토한다.

```text
좋은 기업뉴스일 뿐 상한가 언어는 아닌가
신규 사실이 아닌가
뉴스 금액이 회사 귀속 금액이 아닌가
MOU·협의·예정·프로토타입 단계인가
이미 선반영됐는가
시총·유통물량상 탄력이 부족한가
대규모 희석이 동반되는가
직접 수혜보다 억지 관련주인가
시장기억은 있지만 현재 관계가 끊겼는가
같은 테마 후보 중 더 순도 높은 종목이 있는가
```

Red-team은 후보를 자동 삭제하지 않고 최종 synthesizer에 반대 증거를 전달한다.

## Pass 7: Final synthesis

최종 LLM은 다음 전체를 함께 본다.

```text
current news
open-world first analysis
web research
global brain
all shard contributions
retrieved raw episodes
positive cases
negative cases
counterexamples
candidate research
red-team output
D-1 market data
```

그 후 최종 결과를 만든다.

# 11. 최종 출력 형식

JSON과 사람이 읽는 Markdown을 동시에 생성한다.

```text
predictions/YYYY-MM-DD.json
reports/YYYY-MM-DD_preopen.md
runs/manifests/<run_id>.json
```

## 11.1 Markdown 보고서

최소 섹션:

```text
1. 실행 정보
2. 연구 두뇌 버전
3. 뉴스 범위와 cutoff
4. 주도섹터 예상
5. 단일뉴스 상한가 후보
6. 테마 수혜주 상한가 후보
7. 전일 대장 연속성 후보
8. 전체 장전 관심종목
9. 제외했지만 주의할 종목
10. 핵심 반례와 불확실성
11. 사용한 과거 연구 사례
12. 추가 웹 조사 출처
13. memory coverage
```

## 11.2 Candidate 필드

```python
Candidate:
    rank
    ticker
    company_name
    path_type
    event_ids
    thesis
    why_now
    causal_chain
    direct_evidence
    inferred_evidence
    market_memory_evidence
    prior_positive_cases
    prior_negative_cases
    novel_reasoning
    counterarguments
    disconfirming_conditions
    confidence_label
    evidence_quality
    source_urls
    memory_episode_ids
```

`path_type`:

```text
SINGLE_EVENT
THEME_BENEFICIARY
CONTINUATION
HYBRID
```

후보가 어떤 경로로 생성됐는지 명확히 표시한다.

## 11.3 주도섹터

```python
DominantSectorHypothesis:
    name
    triggering_events
    formation_mechanism
    expected_breadth
    direct_beneficiaries
    indirect_beneficiaries
    narrative_beneficiaries
    possible_leaders
    failure_conditions
    supporting_cases
    contradicting_cases
```

섹터명은 고정 taxonomy에서 선택하지 않아도 된다.
LLM이 자연어로 새 섹터를 생성할 수 있어야 한다.

## 11.4 확률 숫자 남발 금지

충분히 calibration되지 않은 상태에서 `73%` 같은 가짜 정밀도를 출력하지 않는다.

초기에는:

```text
very_high
high
medium
low
speculative
```

처럼 qualitative confidence를 사용한다.

과거 데이터로 실제 calibration이 구축된 이후에만 통계 확률을 병기한다.

# 12. Context Manifest

매 실행에서 LLM이 어떤 기억을 받았는지 완전히 재현 가능해야 한다.

```python
ContextManifest:
    run_id
    brain_version
    brain_files
    brain_file_hashes
    accepted_episode_count
    swept_episode_count
    swept_episode_ids
    retrieved_episode_ids
    counterexample_episode_ids
    token_counts
    truncations
    web_queries
    web_sources
    price_snapshot
    model_config
    prompt_hashes
```

exhaustive mode에서:

```text
swept_episode_count == accepted_episode_count
```

가 아니면 실행을 실패시킨다.

어떤 연구가 컨텍스트에서 빠졌다면 조용히 진행하지 말고 manifest에 오류로 남긴다.

# 13. Research import와 brain update 사용 흐름

사용자는 미래에 다음과 같이 사용한다.

```bash
nslab research import research_output_2024_06_17.md
nslab research validate <episode_id>
nslab research accept <episode_id>
nslab brain update --episode <episode_id>
```

여러 개 일괄 처리:

```bash
nslab research import-batch data/inbox/research/
nslab brain rebuild --mode full
nslab brain audit
```

새 연구가 들어와도 source code는 바뀌지 않아야 한다.

중요 acceptance test:

```text
새 research episode를 추가
→ src/ 파일 변경 없음
→ brain snapshot과 memory data만 변경
→ 이후 분석 결과에 새 교훈이 반영됨
```

# 14. 평가와 워크포워드

다음 명령을 구현한다.

```bash
nslab evaluate --trade-date YYYY-MM-DD
```

처리:

1. 봉인된 blind prediction을 불러온다.
2. stock-web에서 D 결과를 읽는다.
3. 상한가 및 상승 라벨을 생성한다.
4. 적중, 누락, 오탐을 계산한다.
5. LLM postmortem을 생성한다.
6. 실패 원인을 구조화한다.
7. 새 episode learning을 생성한다.
8. 다음 거래일부터 사용할 수 있게 `available_from`을 설정한다.

실패 분류:

```text
INPUT_MISSING
ENTITY_MISSING
THEME_MAP_MISSING
CONTINUATION_MISSING
RANKING_MISS
TIMING_IMPOSSIBLE
NOVELTY_ERROR
DIRECTNESS_ERROR
LEADER_SELECTION_MISS
MARKET_REGIME_MISS
HINDSIGHT_CONTAMINATION
UNKNOWN
```

이 값들은 분석 결과를 정하는 하드코딩 규칙이 아니라 평가용 오류 분류다.

## 14.1 성능지표

최소:

```text
UpperLimit Recall@5
UpperLimit Recall@10
UpperLimit Recall@20
Precision@5
Precision@10
Theme Recall
Single-event Recall
Beneficiary Recall
Continuation Recall
Average max return of top N
Gap-up hit rate
False-positive rate
```

상한가만 정답으로 쓰지 않는다.

다음 label도 함께 평가한다.

```text
고가 +5%
고가 +10%
고가 +15%
고가 +20%
상한가 터치
상한가 마감
```

## 14.2 Walk-forward

미래정보 누수를 막기 위해:

```text
D 분석
→ D 이전에 available한 brain만 사용
→ D 결과 공개
→ D postmortem을 D+1 brain에 반영
```

구조를 강제한다.

과거 전체 연구를 미리 완성한 뒤 예전 날짜에 미래 교훈을 넣는 naive backtest를 금지한다.

# 15. 연구 두뇌가 진짜 일반화하는지 검증하는 테스트

## 15.1 No-domain-hardcoding scan

production `src/`를 검사해:

* 실제 한국 종목명
* 실제 6자리 티커
* 특정 지역→섹터 dictionary
* 특정 뉴스표현→점수표
* 특정 테마→종목 리스트

가 발견되면 테스트 실패.

테스트 fixture에는 실제 회사 대신 가짜 이름과 가짜 코드만 사용한다.

## 15.2 Novel region metamorphic test

같은 구조의 사건에서 지역명만 바꾼다.

```text
A지역에 대규모 첨단산업단지 건설
B지역에 대규모 첨단산업단지 건설
```

exact keyword map 없이도 둘 다:

```text
건설
전력
용수
물류
지역 자산
과거 시장기억
```

과 같은 작동 경로를 새로 추론할 수 있어야 한다.

결과 종목을 고정할 필요는 없지만 분석 절차와 메커니즘이 일반화되어야 한다.

## 15.3 Retrieval-miss test

관련 과거 사례 검색 결과를 강제로 0건으로 만든다.

그래도 open-world pass와 web research를 통해:

* 사건 메커니즘
* 섹터 가설
* 신규 후보 조사계획

을 생성해야 한다.

빈 후보 목록을 반환하면 실패.

## 15.4 New company test

company memory에 없는 가상 상장사가 뉴스에 등장한다.

LLM과 web/company tool을 통해 신규 company memory 후보를 만들고 분석해야 한다.

기존 목록에 없다는 이유로 탈락하면 실패.

## 15.5 Research addition test

새 연구 episode를 추가했을 때:

* 코드 변경 없음
* brain version 증가
* coverage 증가
* 새 claim 또는 반례가 생성
* 이후 context manifest에 포함

되어야 한다.

## 15.6 Blind price leak test

D 예측 중 D 가격 요청은 반드시 실패해야 한다.

## 15.7 Web time leak test

cutoff 이후 기사만 검색 결과로 주면 blind evidence에서 제외되어야 한다.

## 15.8 Exhaustive coverage test

accepted episode가 100개라면 exhaustive run manifest의 swept episode도 100개여야 한다.

## 15.9 Brain rebuild determinism

동일한 원본, 동일한 모델설정, 동일한 seed/config에서는 manifest와 구조적 output이 재현 가능해야 한다.
텍스트의 완전 동일성이 어려우면 semantic fields와 provenance coverage를 검증한다.

# 16. LLM provider 및 도구 설계

최소 interface:

```python
class LLMProvider(Protocol):
    async def generate_structured(...)
    async def generate_text(...)
    async def embed(...)

class WebResearchProvider(Protocol):
    async def search(...)
    async def open(...)
    async def verify_timestamp(...)

class PriceSource(Protocol):
    def get_history(...)
    def get_snapshot(...)
    def get_outcome(...)

class MemoryStore(Protocol):
    def add_episode(...)
    def search_semantic(...)
    def list_all_episodes(...)
    def get_available_as_of(...)
```

LLM 호출은 모두:

* prompt version
* input hash
* output
* model config
* tool calls
* retries
* token usage
* timestamp

를 trace에 저장한다.

실패 시 checkpoint에서 재개할 수 있어야 한다.

# 17. 비용 및 캐시

exhaustive 모드는 비용이 클 수 있으므로:

* brain version
* current news hash
* episode shard hash
* prompt version
* model config

조합으로 shard 결과를 캐시한다.

같은 current news와 같은 brain version이면 불필요하게 다시 호출하지 않는다.

중간 실패 시 완료된 shard는 재사용한다.

동시성은 config로 제한한다.

# 18. 웹 UI 또는 로컬 대시보드

핵심 CLI 완성 후 최소한의 로컬 UI를 제공하라.

기능:

```text
뉴스 CSV 업로드
trade date와 cutoff 선택
brain version 표시
exhaustive / brain / fast 모드 선택
분석 시작
진행 중 shard 상태 표시
최종 주도섹터 표시
단일뉴스 후보
수혜주 후보
연속성 후보
근거와 반례 펼쳐보기
context manifest 다운로드
JSON/MD 다운로드
```

Streamlit 또는 FastAPI + 간단한 frontend 중 저장소 상황에 맞는 방식을 선택한다.

UI가 핵심 엔진과 비즈니스 로직을 중복 구현하면 안 된다.

# 19. ChatGPT/GPT Web용 세션 팩

API 자동 실행 외에 사용자가 GPT Web Pro 세션에 직접 넣을 수 있는 export 기능도 구현한다.

```bash
nslab context export-session-pack \
  --news <csv> \
  --trade-date YYYY-MM-DD \
  --mode brain
```

출력:

```text
session_packs/YYYY-MM-DD/
├─ system_instructions.md
├─ research_brain.md
├─ memory_cases.md
├─ current_news.md
├─ company_memory.md
├─ market_context.md
└─ manifest.json
```

이 session pack은 새 GPT 세션에서도 장기 연구 두뇌를 최대한 재현할 수 있어야 한다.

토큰 한도를 넘으면:

* global brain은 항상 포함
* 모든 shard brain 포함
* 관련 원문 사례 포함
* 누락 및 압축 내용을 manifest에 명시

해야 한다.

조용히 일부 연구를 버리면 안 된다.

# 20. 향후 실제 모델 학습을 위한 export

현재 단계에서는 파인튜닝을 실행하지 않아도 되지만, 연구가 충분히 쌓였을 때를 위해 다음 명령을 구현한다.

```bash
nslab training export-sft
nslab training export-preference
nslab training export-evals
```

학습자료는 다음을 분리한다.

```text
blind reasoning examples
theme formation examples
beneficiary discovery examples
leader selection comparisons
positive vs negative candidate preferences
failure correction examples
```

결과를 본 뒤 끼워 맞춘 설명을 blind 정답으로 내보내지 않게 한다.

# 21. AGENTS.md 내용

루트 `AGENTS.md`에는 짧고 강하게 다음을 넣어라.

```text
- 이 저장소는 LLM-native 뉴스 스캘핑 연구 시스템이다.
- production 코드에 종목, 티커, 테마, 지역→수혜주 매핑을 하드코딩하지 않는다.
- 연구지식은 code가 아니라 research/memory/brain에 저장한다.
- exact keyword retrieval은 참고 수단일 뿐 판단 게이트가 아니다.
- 후보 생성은 항상 open-world pass로 시작한다.
- 신규 연구 추가는 source code 수정 없이 반영되어야 한다.
- blind inference에서 당일 가격과 cutoff 이후 정보 접근을 금지한다.
- 모든 output은 provenance와 context manifest를 가진다.
- 테스트, ruff, mypy를 통과해야 완료다.
```

AGENTS.md에 거대한 연구내용을 복사하지 않는다.

# 22. Codex repo skill

`.agents/skills/news-scalping-lab/SKILL.md`를 구현한다.

이 skill은 다음 작업에서 사용한다.

```text
연구 episode import
brain update/rebuild
brain audit
daily blind analysis
postmortem evaluation
lookahead leak audit
hardcoding audit
```

skill은 명령, 입력, 예상 출력, 실패 시 복구 절차를 설명한다.
도메인 지식을 skill에 박지 않는다.

# 23. CLI

최소 명령:

```bash
nslab init
nslab doctor

nslab news inspect <csv>
nslab news import <csv>

nslab research import <path>
nslab research import-batch <directory>
nslab research validate <episode_id>
nslab research accept <episode_id>
nslab research reject <episode_id>

nslab brain update --episode <episode_id>
nslab brain rebuild --mode full
nslab brain audit
nslab brain diff <version_a> <version_b>

nslab analyze --news <csv> --trade-date <date> --cutoff <timestamp> --mode exhaustive --web-search
nslab evaluate --trade-date <date>

nslab context inspect <run_id>
nslab context export-session-pack ...

nslab audit hardcoding
nslab audit lookahead
nslab audit provenance
nslab audit coverage

nslab training export-sft
nslab training export-preference
nslab training export-evals
```

`nslab doctor`는:

```text
환경변수
API 연결
stock-web 경로
DB 상태
brain HEAD
accepted episode 수
vector index
schema version
```

을 확인한다.

# 24. 사용자 경험 예시

최종 사용 흐름이 실제로 동작해야 한다.

```bash
# 초기화
nslab init

# 연구 결과 추가
nslab research import-batch data/inbox/research/
nslab brain rebuild --mode full
nslab brain audit

# 당일 장전 분석
nslab analyze \
  --news data/inbox/news/0700_news_20260715.csv \
  --trade-date 2026-07-15 \
  --cutoff 2026-07-15T08:59:59+09:00 \
  --mode exhaustive \
  --web-search

# 장 종료 후
nslab evaluate --trade-date 2026-07-15
nslab brain update --episode 2026-07-15
```

# 25. 구현 순서

한 작업 안에서 다음 순서로 진행하라.

1. 기존 저장소를 전부 조사한다.
2. `PLANS.md`에 실행계획을 작성한다.
3. project scaffold와 config를 만든다.
4. canonical Pydantic models와 JSON schemas를 만든다.
5. immutable research import를 만든다.
6. DuckDB/Parquet memory store를 만든다.
7. LLM provider와 mock provider를 만든다.
8. stock-web adapter와 blind guard를 만든다.
9. brain compiler와 coverage audit를 만든다.
10. context assembler와 exhaustive memory sweep를 만든다.
11. daily analyst pipeline을 만든다.
12. web temporal guard를 만든다.
13. report 및 JSON 출력기를 만든다.
14. outcome evaluator를 만든다.
15. hardcoding 및 lookahead audits를 만든다.
16. CLI를 연결한다.
17. 최소 로컬 UI를 만든다.
18. AGENTS.md와 Codex skill을 만든다.
19. README를 작성한다.
20. unit, integration, metamorphic tests를 작성한다.
21. mock data로 end-to-end demo를 실행한다.
22. ruff, mypy, pytest를 모두 통과시킨다.
23. 실패를 수정한다.
24. 최종 실행 예시와 결과를 제출한다.

# 26. 구현 완료 기준

다음이 모두 충족되어야 완료다.

* 새 연구자료가 코드 수정 없이 import된다.
* 새 연구자료가 brain version에 반영된다.
* 모든 accepted episode가 coverage manifest에 포함된다.
* exhaustive 모드가 전체 연구를 sweep한다.
* retrieval 결과가 없어도 신규 사건을 분석한다.
* production code에 실제 종목·티커·테마 매핑이 없다.
* D 가격 및 cutoff 이후 정보 누수가 차단된다.
* 새 CSV 하나로 최종 장전 보고서가 생성된다.
* 보고서가 주도섹터, 단일뉴스 후보, 수혜주 후보, 연속성 후보를 분리한다.
* 과거 성공사례와 실패사례가 함께 제공된다.
* 모든 판단에 provenance가 있다.
* context manifest로 실행을 재현할 수 있다.
* mock end-to-end test가 통과한다.
* ruff, mypy, pytest가 통과한다.
* README 명령을 그대로 실행할 수 있다.

# 27. 구현 중 의사결정 원칙

요구사항 간 충돌이 생기면 다음 우선순위를 따른다.

1. 미래정보 누수 방지
2. 연구지식의 코드 하드코딩 방지
3. 전체 연구 coverage
4. 새로운 사건에 대한 오픈월드 일반화
5. 출처 및 실행 재현성
6. 분석 품질
7. 실행 비용
8. 속도

비용이나 속도를 이유로 연구 coverage와 오픈월드 추론을 몰래 제거하지 마라.

# 28. 최종 응답 방식

작업 완료 후 최종 응답에는 다음만 명확히 보고한다.

1. 구현한 아키텍처
2. 핵심 파일
3. 하드코딩 방지 장치
4. 연구 두뇌 갱신 방식
5. exhaustive context 방식
6. 실행 명령
7. 테스트 결과
8. mock end-to-end 결과
9. 실제 API 실행에 필요한 환경변수
10. 남은 외부 의존성

계획만 설명하지 말고 실제 구현 결과를 보고하라.

```

이 프롬프트의 핵심은 **“연구자료를 검색해서 맞는 문서를 못 찾으면 포기”하는 구조를 아예 금지**하고, 품질 모드에서는 현재 뉴스를 **모든 연구 episode와 shard 단위로 직접 대조**하게 만든다는 점이야. 연구가 추가될 때 변경되는 것은 코드가 아니라 `episode → memory → brain snapshot`이고, Codex가 이후에도 이 원칙을 지키도록 `AGENTS.md`와 repo skill까지 생성하게 해뒀어. 반복 실행은 이후 `codex exec` 같은 비대화형 작업에도 연결할 수 있어.