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