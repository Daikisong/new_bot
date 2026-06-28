# NSLAB 운영 단계 정리

이 문서는 연구 번들을 모아 프로젝트 두뇌로 쓰기까지의 전체 단계를 정리한다.

## 현재 목표

현재 목표는 `example2.md`급 연구 번들을 날짜별로 모아서 프로젝트의 장기기억 원료로
누적하는 것이다.

핵심은 아래 흐름이다.

```text
GPT Pro 연구
→ 연구 MD 저장
→ repair
→ import
→ memory / warehouse / brain 누적
→ Codex 또는 자체 엔진이 두뇌로 활용
```

## Phase 1. 연구 프롬프트 고정

목표:

```text
docs/research_prompt.md
docs/session_prompt.md
docs/example2.md
```

이 세 파일 기준으로 웹 GPT Pro 세션이 안정적으로 연구 번들을 만들게 한다.

통과 기준:

```text
BLIND 전 가격 접근 없음
CSV 전체 파싱
후보 생성/탈락/최종 선정 근거 기록
postmortem에서 가격 결과와 뉴스 근거 재대조
brain_delta record 생성
direct ingest 가능한 bundle shell 생성
```

현재 상태:

```text
기본 방향 통과
example2.md를 gold shape 기준으로 사용
추가 연구 번들을 계속 생산하는 단계
```

## Phase 2. 연구 번들 수집

웹 GPT Pro에서 생성한 원본 연구 MD는 아래 폴더에 모은다.

```text
research/inbox/bundles/raw/
```

파일럿 성공 예시는 아래 폴더에 보관한다.

```text
docs/pilot_bundles/
docs/pilot_repairs/
```

파일럿 폴더는 비교 기준이다. 실전 누적용으로 계속 쓰지 않는다.

## Phase 3. repair / inspect / import

원본 MD는 바로 두뇌에 넣지 않는다. 먼저 repair 후 검증한다.

```text
research/inbox/bundles/raw/*.md
→ research/inbox/bundles/repaired/*.repaired.md
```

수행 절차:

```text
repair
inspect-bundle
import-bundle --validate --accept
warehouse rebuild
warehouse verify
brain rebuild --mode catalog --allow-catalog
brain audit --deep
```

Codex에게 요청할 때는 이렇게 말하면 된다.

```text
research/inbox/bundles/raw에 있는 연구들 docs/설명서.md대로 진행해줘
```

통과한 번들은 아래에 누적된다.

```text
research/episodes/
memory/records/
memory/record_manifests/
warehouse/
brain/current/
```

## Phase 4. 두뇌 원료 누적

연구 번들이 import되면 단순 문서가 아니라 record 단위의 장기기억이 된다.

주요 record 예:

```text
supervised_direct_event_case
supervised_issuer_day_case
negative_control_case
candidate_ranking_error_case
beneficiary_discovery_case
newsless_or_unexplained_case
context_market_state_or_fact_case
```

이 record들은 아래 정보를 담는다.

```text
어떤 뉴스가 있었는지
어떤 후보를 왜 골랐는지
어떤 후보를 왜 탈락시켰는지
실제 가격 결과가 어땠는지
무엇이 맞고 틀렸는지
다음 판단에 어떤 교정이 필요한지
```

`training_eligible_record_count`는 그중 학습 원료로 바로 써도 되는 record 수다.
나머지는 버린 것이 아니라 audit/context/provenance로 보존된다.

## Phase 5. Codex + 두뇌 개인 운영

개인 사용에서는 Codex를 조종석이자 LLM provider처럼 운용할 수 있다.

이 경우 구조는 아래와 같다.

```text
사용자
→ 새 CSV 또는 새 연구 MD 제공
→ Codex에게 명령
→ Codex가 repo 안의 brain/memory/warehouse를 읽음
→ Codex가 과거 연구 record를 참고해 분석/repair/import/audit 수행
```

이 방식은 Codex 자체를 파인튜닝하는 것이 아니다.

정확한 의미:

```text
Codex 모델이 영구적으로 변함 = 아님
Codex가 프로젝트의 장기기억 파일을 읽고 활용함 = 맞음
```

혼자 쓰는 반자동 운영이면 이 방식으로 충분히 가능하다.

예:

```text
새 뉴스 CSV 입력
→ Codex에게 "두뇌 참고해서 분석해줘"
→ Codex가 과거 성공/실패/반례/negative control record를 읽음
→ 현재 후보 생성과 순위 판단에 참고
→ 결과가 나오면 새 연구로 다시 누적
```

## Phase 6. 자체 production brain

완전 자동 CLI/서버 운영을 하려면 프로젝트 내부 provider를 실제 서비스로 연결해야 한다.

provider 예:

```text
LLM provider: OpenAI API 같은 실제 추론 모델
embedding provider: 연구 record 의미 검색용 벡터 임베딩
web provider: 웹/공시/뉴스 확인
stock-web provider: 가격 outcome snapshot 조회
```

이 단계가 필요한 경우:

```text
Codex에게 매번 지시하지 않고 프로젝트가 자체적으로 분석해야 할 때
서버/스케줄러/자동 파이프라인으로 돌릴 때
대량 검색과 후보 추론을 API 기반으로 자동화할 때
```

이때 목표 명령은 아래 쪽이다.

```bash
python -m news_scalping_lab.cli brain rebuild --mode llm-full
python -m news_scalping_lab.cli brain audit --deep
python -m news_scalping_lab.cli doctor --production
```

## 현재 권장 운영

지금은 production provider보다 연구 누적이 우선이다.

권장 순서:

```text
1. GPT Pro에서 날짜별 연구 번들 생성
2. research/inbox/bundles/raw/에 저장
3. Codex에게 docs/설명서.md대로 repair/import/audit 요청
4. 5~10개 단위로 통과 여부 확인
5. 통과율이 안정되면 더 많은 날짜로 확장
6. 충분히 쌓이면 Codex + 두뇌로 실제 새 CSV 분석
7. 필요할 때 자체 production provider 연결
```

## 한 줄 결론

```text
지금 단계 = 좋은 연구 번들을 많이 모아 장기기억 record로 누적하는 단계
개인 운영 = Codex가 repo brain을 읽어 두뇌처럼 활용
완전 자동 운영 = real provider를 붙여 프로젝트 자체 production brain으로 승격
```

