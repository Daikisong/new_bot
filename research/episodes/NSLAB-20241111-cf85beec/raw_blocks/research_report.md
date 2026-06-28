# blind_report.md

## 1. 입력·거래일 감사

trade_date=2024-11-11; previous_trade_date=2024-11-08; input_sha256=cf85beec92a1924bdb1d42cc8cbcfdaa2ee6f76647b386f7da20317a2593589b; row_count=2072; coverage=2024-11-08 15:31:36~2024-11-11 08:58:54; time_unverified_rows=0.

## 2. research_daily access·schema 검증

access JSON web-view metadata: blind_snapshot_path=atlas/research_daily/snapshots/2024/11/20241108.csv; outcome_snapshot_path locked post-seal; blind_row_count=2620; outcome_row_count=2619.

## 3. BLIND snapshot 안전성·해시 검증

blind_snapshot_sha256=944fe4faacd5fcf02662763fd7b78aa945f069f5a6d2353e9155180fa1f5b469; byte_size=879502; rows=2620; only P snapshot columns used before seal.

## 4. BLIND 무결성·패킷 봉인

D outcome bytes are not downloaded before this report and packet receipt are rendered; preseal_outcome_access_all_zero=true.

## 5. 뉴스 행 전수 분류 커버리지

{"csv_row_count": 2072, "parsed_row_count": 2072, "source_ledger_count": 2072, "source_ledger_news_row_count": 2072, "row_disposition_count": 2072, "material_review_queue_count": 578, "material_reviewed_count": 578, "material_review_unreviewed_count": 0, "fact_ledger_blind_count": 229, "inference_ledger_blind_count": 229, "event_observation_count": 578, "candidate_screening_count": 471, "rankable_candidate_count": 56, "final_watchlist_count": 20, "time_unverified_rows": 0}

## 6. BLIND 엔티티 의미 정확도

entity_resolution_count=123; exact title/local predicate binding is required; ambiguous literals remain audit-only.

## 7. Atomic Fact·Inference 품질

fact_count=229; inference_count=229; every fact has source_id/material_review_id/exact_quote; quote_role=LOCAL_PREDICATE_EVIDENCE.

## 8. 직접 기업뉴스 관측 모집단

direct_issuer_fact_candidates=122; observation_count=578.

## 9. 모든 observation 후보 심사

candidate_screening_count=471; all material observations and audit-only rows link by material_review_id.

## 10. 사건 지도

dominant lanes: issuer financing/ownership, AI/robot/biotech R&D, defense/space, earnings/value-up, D-1 continuation.

## 11. 오픈월드 최초 분석

Open-world context retained via THEME_OR_MARKET_CONTEXT screening records; no ticker is promoted from a theme row without issuer binding.

## 12. 주도섹터 가설과 sealed peer universe

트럼프(120); AI(29); 바이오(16); 비트코인(15); 반도체(13); 배터리(8); 전기차(8); 방산(6); 우크라이나(4); 금투세(3)

## 13. 단일뉴스 후보

single/direct issuer candidate universe count=122; repeated rows are merged by issuer.

## 14. 테마 수혜 archetype·후보

AI/robot/biotech/defense/space/K-beauty/value-up lanes are sealed from CSV rows and P snapshot only.

## 15. D-1 연속성 후보

D-1 continuation rows included only when title has direct issuer price-action predicate; table/list rows are audit-only.

## 16. BLIND pairwise 비교

rankable=56; final_count=20; sorting uses reparsed candidate_screening blind_score after population closure.

## 17. 최종 장전 관심종목

1. 035420 NAVER score=36.2 quote=네이버의 AI 승부수…"쇼핑·지도·부동산에 다 붙인다"
2. 005440 현대지에프홀딩스 score=35.9 quote=현대지에프홀딩스, 현대이지웰 지분 15% 공개매수
3. 373220 LG에너지솔루션 score=35.9 quote=포트폴리오 다변화…LG엔솔, 올 초대형 수주 5건
4. 021240 코웨이 score=35.4 quote="역대 분기 최대 실적"…코웨이, 3Q 영업익 2071억(종합)
5. 460850 동국씨엠 score=33.4 quote=동국씨엠, 아주스틸 인수 완료…컬러강판 세계 1위로 우뚝
6. 196170 알테오젠 score=31.6 quote=알테오젠, 日에 피하주사제 3억弗 기술 수출
7. 102940 코오롱생명과학 score=23.6 quote=코오롱생명과학, 골관절염 세포치료제 유효성 평가법 싱가포르 특허 등록
8. 079550 LIG넥스원 score=23.1 quote=LIG넥스원, 3분기 영업익 519억 26.5%↑…수주 낭보 지속 기대
9. 326030 SK바이오팜 score=21.8 quote=SK바이오팜, 엑스코프리 매출 증가로 실적 개선…목표가↑-한국
10. 403550 쏘카 score=21.6 quote=쏘카, LG전자와 스마트 충전 스테이션 기술 협력
11. 079160 CJ CGV score=20.0 quote=CJ CGV, 6분기 연속 흑자…매출 5천470억원·영업이익 321억원
12. 047810 한국항공우주 score=19.8 quote="한국항공우주, 내년에도 실적 성장 전망"-유안타
13. 042660 한화오션 score=19.3 quote=한화오션 치켜세운 캐나다 해군총장, '60조' 수주로 이어지나
14. 122870 와이지엔터테인먼트 score=19.1 quote="YG엔터, 내년 블핑·베몬·위너·2NE1 모두 온다…목표가↑"-삼성
15. 001040 CJ score=18.3 quote=AI 스마트팩토리 등 사업 수주에…CJ올리브네트웍스, 3Q 견조한 실적
16. 069960 현대백화점 score=18.1 quote=현대百 지주사, ‘복지몰’ 현대이지웰 지분 15% 공개매수
17. 046890 서울반도체 score=17.0 quote=서울반도체, 3분기 영입익 38억원…2분기 연속 흑자
18. 241710 코스메카코리아 score=17.0 quote=코스메카코리아, 3분기 영업익 152억원…전년 대비 11% ↑
19. 012450 한화에어로스페이스 score=16.8 quote=한화에어로, 중동·유럽서 추가수주 기대 [株슐랭가이드]
20. 031440 신세계푸드 score=16.8 quote=[잠정실적]신세계푸드 3Q 실적, 영업이익 84.7억원... 전년동기 대비 8.8% 증가 (연결)

## 18. BLIND Red-team

red-team exclusions include financing dilution, target-price cuts, weak local predicate binding, table/list-only appearances, and large-cap sellside-only rows.

## 19. BLIND packet manifest

blocks: source_ledger,row_disposition,material_review_queue,material_review,entity_resolution,fact_ledger_blind,inference_ledger_blind,event_observation_population,candidate_screening,candidate_ranking_audit,final_watchlist,final_evidence_witness.


--- BLIND 봉인 이후 결과 공개 ---

# postmortem_report.md

## 20. OUTCOME snapshot 완전성·해시 검증

outcome_sha256=47f70bff7347a80fde2e36bf7ad69f5cbac746cac4218fa6ae38a7eea6ea876c; bytes=883119; rows=2619; expected_sha256=47f70bff7347a80fde2e36bf7ad69f5cbac746cac4218fa6ae38a7eea6ea876c.

## 21. Post-seal 엔티티 확정

outcome symbols=2619; final candidates joined=20/20.

## 22. 전 시장 상한가·강한 상승 census

{"upper_limit_touched": 11, "upper_limit_closed": 7, "high_ge_20": 27, "high_ge_15": 41, "high_ge_10": 73}
LEAD-00001 001470 삼부토건 high=30.00% close=30.00% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00002 100790 미래에셋벤처투자 high=30.00% close=24.89% labels=UPPER_LIMIT_TOUCHED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00003 270520 지오릿에너지 high=30.00% close=30.00% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00004 002410 범양건영 high=29.97% close=29.97% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00005 475960 토모큐브 high=29.95% close=23.76% labels=UPPER_LIMIT_TOUCHED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00006 057680 티사이언티픽 high=29.95% close=9.77% labels=UPPER_LIMIT_TOUCHED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00007 105330 케이엔더블유 high=29.94% close=29.94% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00008 017860 DS단석 high=29.93% close=29.93% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00009 003535 한화투자증권우 high=29.93% close=19.86% labels=UPPER_LIMIT_TOUCHED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00010 225190 LK삼양 high=29.83% close=29.83% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00011 227950 엔투텍 high=29.79% close=29.79% labels=UPPER_LIMIT_TOUCHED,UPPER_LIMIT_CLOSED,HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00012 222110 팬젠 high=29.72% close=20.37% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00013 062860 티엘아이 high=29.14% close=23.84% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00014 051490 나라엠앤디 high=27.13% close=12.74% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00015 357880 비트나인 high=26.65% close=10.85% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00016 223310 딥마인드 high=25.39% close=0.00% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00017 246720 아스타 high=25.03% close=13.14% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00018 199480 뱅크웨어글로벌 high=24.20% close=16.56% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00019 035080 그래디언트 high=23.16% close=14.56% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00020 082800 비보존 제약 high=23.11% close=20.39% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00021 003530 한화투자증권 high=23.10% close=17.18% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00022 087010 펩트론 high=22.75% close=18.35% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00023 419080 엔젯 high=21.90% close=16.48% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00024 219550 디와이디 high=21.86% close=14.42% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10
LEAD-00025 348080 큐라티스 high=21.70% close=0.48% labels=HIGH_GE_20,HIGH_GE_15,HIGH_GE_10

## 23. forecast scorecard

1. 035420 NAVER high=1.37% close=0.80% label=MISS
2. 005440 현대지에프홀딩스 high=0.71% close=-1.83% label=MISS
3. 373220 LG에너지솔루션 high=9.28% close=4.39% label=NEAR_MISS_HIGH8
4. 021240 코웨이 high=0.81% close=-1.61% label=MISS
5. 460850 동국씨엠 high=0.90% close=-2.26% label=MISS
6. 196170 알테오젠 high=4.23% close=1.95% label=MISS
7. 102940 코오롱생명과학 high=0.16% close=-2.76% label=MISS
8. 079550 LIG넥스원 high=2.30% close=1.92% label=MISS
9. 326030 SK바이오팜 high=3.04% close=1.43% label=MISS
10. 403550 쏘카 high=1.19% close=1.13% label=MISS
11. 079160 CJ CGV high=0.90% close=-2.35% label=MISS
12. 047810 한국항공우주 high=9.05% close=7.78% label=NEAR_MISS_HIGH8
13. 042660 한화오션 high=8.70% close=3.04% label=NEAR_MISS_HIGH8
14. 122870 와이지엔터테인먼트 high=2.38% close=-2.04% label=MISS
15. 001040 CJ high=-0.49% close=-5.93% label=MISS
16. 069960 현대백화점 high=0.00% close=-2.93% label=MISS
17. 046890 서울반도체 high=0.00% close=-7.66% label=MISS
18. 241710 코스메카코리아 high=-8.96% close=-16.04% label=MISS
19. 012450 한화에어로스페이스 high=3.59% close=3.10% label=MISS
20. 031440 신세계푸드 high=0.00% close=-1.01% label=MISS

## 24. issuer-day 감독학습 모집단

issuer_day_case_count=20; final_hit_high10=0; near_miss_high8=3.

## 25. 직접뉴스 event-level 감독학습 모집단

direct_event_case_count=229; all direct BLIND facts joined to D outcome with fractional event weights.

## 26. 후보 생성·순위·event thesis 오류

outcome_to_news classifications: SCREENED_OUT_BUT_WINNER=2; NEWSLESS_OR_UNEXPLAINED=67; BODY_TABLE_HIT=1; RANKING_MISS=2; DIRECT_NAME_HIT_WEAK_BINDING=1. Main miss: 삼부토건 was screened out because 투자경고/overhang red-team outweighed continuation, but became an upper-limit leader.

## 27. 주도섹터 형성 연구 — sealed universe 기준

Observed winner clusters: Trump/Ukraine reconstruction and construction, crypto/VC/fintech beta, defense/space/shipbuilding, biotech/platform drugs, robotics/sensors.

## 28. retrospective theme discovery

The market rewarded broad beta ladders more than many single-company earnings/revision items; table/context rows were useful for discovery but not allowed as direct issuer evidence.

## 29. 수혜주 발견 연구

Beneficiary discovery rows in outcome_to_news_audit mark leaders with weak/body/table/context preseal traces rather than local issuer predicate.

## 30. 대장 선택 correction·confirmation 연구

Leader selection should have separated continuation/fatigue from dilution: 삼부토건 had both a continuation row and an investment-warning row; the red-team was directionally valid risk control but too strict for momentum tape.

## 31. 후보 실패·부정 대조군

negative_control_source_count=40; final misses include cosmetics/consumer earnings that lacked D-day tape confirmation.

## 32. 행·엔티티·ticker binding 오류

Known weak spots repaired into audit logic: short literals, table/list rows, people/appointment rows, and partner-not-subject rows. Remaining limitations are recorded in section 36.

## 33. 학습 적격성 매트릭스

training_eligible_records=369; excluded_context_records=89; provenance_closure=passed.

## 34. Brain Delta 요약

brain_delta_records=458; expected_min=422; record_type_counts={"supervised_issuer_day_case": 20, "supervised_direct_event_case": 229, "rankable_candidate_case": 54, "candidate_ranking_error_case": 2, "ranking_error_case": 4, "newsless_or_unexplained_case": 67, "beneficiary_discovery_case": 2, "selected_negative_control_source": 40, "context_market_state_or_fact_case": 20, "blind_leader_preference_pair": 20}.

## 35. 다음 연구 질문

Test whether post-close continuation rows with explicit upper-limit/Trump/Ukraine wording should enter a separate momentum lane instead of being red-teamed solely by 투자경고.

## 36. 출처·데이터 한계

News CSV supplies title/body rows and stock-web supplies raw/unadjusted FinanceData/marcap outcome snapshots. Semantic review is artifact-bound and conservative; absence of a direct CSV row does not prove the market had no external catalyst.