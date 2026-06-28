# NSLAB Gold Research Pilot TODO

목표: 5년치 대량 연구 전에 `docs/example2.md` 같은 gold bundle을 5~10개 안정적으로 만들고, import/warehouse/training/catalog smoke까지 실제로 통과하는지 확인한다.

## 1. 연구 프롬프트 완벽 정립

- [ ] 현재 기준 파일을 고정한다.
  - production prompt: `docs/research_prompt.md`
  - web session runner: `docs/session_prompt.md`
  - gold output reference: `docs/example2.md`
- [ ] 새 웹 세션에서는 `docs/session_prompt.md`만 붙여넣는다.
- [ ] 세션마다 수동으로 바꿀 값은 `이번 세션 시작 파일: news_YYYYMMDD.csv` 하나로 제한한다.
- [ ] 파일럿 중에는 blocker가 나오기 전까지 prompt를 계속 수정하지 않는다.
- [ ] blocker가 나오면 결과 MD, 추론 로그, 실패 검증 항목을 먼저 기록한 뒤 최소 패치만 한다.

## 2. 파일럿 날짜 5~10개 선정

- [ ] 이미 많이 사용한 날짜는 제외하고 미사용 CSV 위주로 고른다.
- [ ] 한 가지 장세만 고르지 말고 서로 다른 성격을 섞는다.
- [ ] 직접 기업뉴스가 강한 날 1~2개를 포함한다.
- [ ] 테마/섹터 상한가가 많은 날 1~2개를 포함한다.
- [ ] 정치/거시 충격일 1~2개를 포함한다.
- [ ] 별 뉴스 없이 수급이나 시장 흐름이 강한 날 1~2개를 포함한다.
- [ ] 약세장/무난한 장/노이즈가 많은 날도 최소 1개 포함한다.

## 3. Gold Bundle 생성

- [ ] 한 웹 세션에서는 CSV 하나만 처리한다.
- [ ] MAIN PROMPT는 반드시 GitHub Raw의 `docs/research_prompt.md`를 새로 확보하게 한다.
- [ ] 가격 outcome은 blind seal 이후에만 접근되어야 한다.
- [ ] 최종 산출물은 실제 다운로드 가능한 `<YYYYMMDD>_nslab_episode_bundle.md` 하나여야 한다.
- [ ] 채팅에 나온 요약이나 코드블록을 수동으로 연구 MD처럼 쓰지 않는다.

## 4. 개별 Bundle 검증

각 결과 파일마다 먼저 inspect를 실행한다.

```bash
python -m news_scalping_lab.cli research inspect-bundle path/to/<YYYYMMDD>_nslab_episode_bundle.md
```

통과 기준:

- [ ] `inspection_status == validation_passed`
- [ ] `validation_passed == true`
- [ ] `schema_version == nslab.research_bundle.v11`
- [ ] `bundle_status == ACCEPT_FULL`
- [ ] `brain_eligible == true`
- [ ] `direct_brain_ingest_ready == true`
- [ ] `raw_record_count == normalized_record_count`
- [ ] `dropped_record_count == 0`
- [ ] `quarantined_record_count == 0`
- [ ] `sample_weight_validation_status == passed`
- [ ] candidate ranking audit가 final 20개뿐 아니라 rankable 후보 탈락 사유까지 닫혀 있다.
- [ ] pre-seal outcome 접근 흔적이 없다.
- [ ] source/provenance/context manifest가 닫혀 있다.

## 5. Pilot Import

검증 통과 파일만 import한다.

```bash
python -m news_scalping_lab.cli research import-bundle path/to/<YYYYMMDD>_nslab_episode_bundle.md --validate --accept
```

각 파일마다 기록할 값:

| date | bundle path | inspect | import | raw records | normalized records | training eligible | sample weight | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| YYYY-MM-DD |  |  |  |  |  |  |  |  |

## 6. 5~10개 누적 후 Downstream Smoke

파일럿 bundle이 5~10개 쌓이면 아래를 실행한다.

```bash
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli warehouse verify
python -m news_scalping_lab.cli training export-sft
python -m news_scalping_lab.cli training export-preference
python -m news_scalping_lab.cli training export-evals
python -m news_scalping_lab.cli training audit
python -m news_scalping_lab.cli brain rebuild --mode catalog --allow-catalog
python -m news_scalping_lab.cli brain audit --deep
```

확인 기준:

- [ ] accepted bundle이 warehouse에 누락 없이 들어간다.
- [ ] raw record와 normalized record 손실이 없다.
- [ ] training export가 `sft`, `preference`, `evals`로 생성된다.
- [ ] duplicate issuer-day, event weight mismatch가 0이다.
- [ ] catalog brain smoke가 통과한다.

## 7. Production Readiness 확인

real provider를 붙이기 전에는 production brain 완성이 아니라는 점을 명시한다.

```bash
python -m news_scalping_lab.cli doctor --production
```

- [ ] mock LLM/embedding/web/stock-web provider 때문에 실패하면 정상 blocker로 기록한다.
- [ ] production 승격은 real LLM, real embedding, real web, stock-web provider 설정 후 다시 판단한다.

## 8. Scale 판단

5~10개 파일럿이 모두 통과하면 다음 단계로 간다.

- [ ] 1개월치 batch
- [ ] 3개월치 batch
- [ ] 1년치 batch
- [ ] 5년치 batch

각 단계마다 `inspect-bundle`, `import-bundle`, `warehouse verify`, `training audit`, `brain audit`를 다시 확인한다.

## 하지 말 것

- [ ] 5~10개 파일럿 통과 전에 5년치 대량 연구를 시작하지 않는다.
- [ ] `docs/example.md` 또는 archived prompt를 gold 기준으로 쓰지 않는다.
- [ ] validation 실패 bundle을 억지로 import하지 않는다.
- [ ] 연구 지식을 source code에 hardcode하지 않는다.
- [ ] exact keyword match를 candidate gate로 쓰지 않는다.
- [ ] prompt가 실패할 때마다 전체 구조를 갈아엎지 않는다.
