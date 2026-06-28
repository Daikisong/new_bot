# blind_report.md

## 1. 입력·거래일 감사
D=2024-12-03, P=2024-12-02, news_rows=1181, min=2024-12-02 15:30:04, max=2024-12-03 08:59:58, input_sha256=797c81f1396c37bba555ad943d12f938590c6b1264feaf489b02f75e211e806c.

## 2. research_daily access·schema 검증
access JSON은 WEB_VIEW_ONLY_UNHASHED로 routing metadata를 확인했고 blind snapshot은 sha256/bytes/row_count로 검증했다.

## 3. BLIND snapshot 안전성·해시 검증
blind_snapshot_date=2024-12-02, sha256=d040186bfa1d0c5614f273c5ec19afd029bac2f02586178103bcf4b3a3b38882, bytes=887148, row_count=2629.

## 4. BLIND 무결성·패킷 봉인
이 보고서와 BLIND JSONL은 outcome snapshot 다운로드 이전에 생성되며, blind_seal_receipt 이후에만 D snapshot을 연다.

## 5. 뉴스 행 전수 분류 커버리지
source_ledger=1181, row_disposition=1181, material_review=565; disposition_counts={'DIRECT_ISSUER_MATERIAL': 94, 'LOW_SIGNAL_CONTEXT': 412, 'THEME_POLICY_INDUSTRY_EVENT': 256, 'MARKET_STATE_REGIME': 97, 'DIRECT_ISSUER_SECONDARY': 16, 'DISCLOSURE_OR_MARKET_NOTICE': 76, 'NON_KR_OR_NON_LISTED_CONTEXT': 163, 'DUPLICATE': 14, 'NON_MARKET_NEWS': 27, 'D1_CONTINUATION_SIGNAL': 2, 'BODY_TABLE_OR_LIST_AUDIT': 24}.

## 6. BLIND 엔티티 의미 정확도
entity_ledger=475, final_semantic_audit=20; final item은 issuer_binding/local predicate owner를 요구한다.

## 7. Atomic Fact·Inference 품질
fact_ledger=475, inference_ledger=475; 모든 fact는 source_row exact_quote를 가진다.

## 8. 직접 기업뉴스 관측 모집단
direct_issuer_observations=120.

## 9. 모든 observation 후보 심사
candidate_screening=565, include/watch=67, ranking_audit=67.

## 10. 사건 지도
주요 축: 전력기기/전력망, 바이오 기술이전·특허, 배터리 JV, 방산/MRO, K뷰티, AI·데이터센터, D-1 모멘텀.

## 11. 오픈월드 최초 분석
시장 전체 테마는 row_disposition/material_review에서 먼저 닫았고, 단일 종목 최종은 candidate_screening 재파싱 이후에만 생성했다.

## 12. 주도섹터 가설과 sealed peer universe
전력기기: LS ELECTRIC·대한전선·전선/변압기, 바이오: 알테오젠·셀리드·딥노이드, 방산: 현대로템·한화오션·한화시스템, 배터리: 삼성SDI·LG에너지솔루션.

## 13. 단일뉴스 후보
계약금/수주/특허/지분인수처럼 원문 row의 경제 변수가 숫자화되는 후보를 우선했다.

## 14. 테마 수혜 archetype·후보
HBM 수출통제는 대형 반도체 리스크, AI센터 냉각·전력망은 인프라 수혜, 소상공인·배달앱은 정책 수혜로 보존했다.

## 15. D-1 연속성 후보
P snapshot의 amount_rank/high_return/turnover는 tie-break context로만 사용했고 direct catalyst를 대체하지 않았다.

## 16. BLIND pairwise 비교
계약 규모·현금 유입·정책 수혜와 P liquidity를 비교하되, negative/dilutive signal은 final에서 제외했다.

## 17. 최종 장전 관심종목
1. 알테오젠(196170) — 알테오젠, 다이이찌산쿄로부터 계약금 2000만달러 수령
2. 삼성SDI(006400) — 美, 삼성SDI·스텔란티스 합작법인에 10조5000억 대출 지원
3. LS ELECTRIC(010120) — 초고압 변압기 잭팟…LS일렉 5600억 계약
4. LG에너지솔루션(373220) — LG엔솔, 美미시건 배터리 합작 공장의 GM 지분 인수(상보)
5. 대한항공(003490) — [IB토마토]대한항공, 아시아나 인수 확정…수익성 개선 본격화
6. 원준(382840) — 그로쓰리서치 "원준, 2차전지 넘어 신규 산업으로 확장 계획 중"
7. 현대건설(000720) — 현대건설, 올해 도시정비사업 수주 6조원 돌파
8. 브이티(018290) — 브이티, 이앤씨 지분 추가 인수…"화장품 시장 경쟁력 강화"
9. 진에어(272450) — 진에어, 무안~오사카·나리타·타이베이 신규 운항
10. 한화오션(042660) — NH證 "한화오션, 美군함 MRO 외형 성장 확대… 목표주가 8% 상향"
11. LG전자(066570) — "AI센터 발열 잡는다"… LG전자 '칠러' 차세대 수출 효자로
12. 딥노이드(315640) — 딥노이드, RSNA서 초록 발표…AI 기반 폐 결절 진단 기술 주목
13. 노브랜드(145170) — 노브랜드, 편안한 성장 속 아웃도어 인수 효과 기대-DS
14. 셀리드(299660) — 셀리드, 코로나19 백신에 적용된 항원 플랫폼 기술 '한국 특허 등록' 결정
15. 카카오(035720) — 카카오, 다음 뉴스서비스 신규 입점 공고
16. HD현대중공업(329180) — NH證 “HD현대중공업, 수익성 더 좋아진다… 목표가 14% 상향”
17. 아이에스동서(010780) — 아이에스동서, 에스케이에코플랜트와 661억원 규모 공급계약
18. 고려아연(010130) — 고려아연, 장중 52주 최고가 150만원 돌파!
19. SK하이닉스(000660) — 김춘환 SK하이닉스 부사장 "요소기술 제때 개발하려면 실패 두려워 말아야"
20. 동아에스티(170900) — 동아에스티 ‘그로트핀’ 전문약 매출 첫 1000억원 돌파

## 18. BLIND Red-team
최종 후보마다 direct quote와 issuer_binding을 확인했다. broker-only, ETF/ELW, table constituent, 타사 기사 단순언급은 탈락 또는 audit-only로 남겼다.

## 19. BLIND packet manifest
blind_packet_manifest.json과 blind_seal_receipt.json을 별도 생성한다.


--- BLIND 봉인 이후 결과 공개 ---

# postmortem_report.md

## 20. OUTCOME snapshot 완전성·해시 검증
outcome_snapshot_date=2024-12-03, sha256=17f13b5b6e9d11e01c5b91ada96103d3b110cefc8bc50236401d94c74ffcbe5e, bytes=884302, row_count=2629, header_columns=46. BLIND seal receipt sha256=acc944dfa5a51ef7e73234787be65db73cac392c03991a911b42faa212ee6e7e 이후에만 outcome Raw를 열고 검증했다.

## 21. Post-seal 엔티티 확정
final_watchlist=20, outcome_rows=2629, ticker join matched=20.

## 22. 전 시장 상한가·강한 상승 census
outcome_leader_census=115. 상위 30: 
CEN-000001 아이윈플러스(123010) high=30.0 close=30.0 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000002 유유제약1우(000225) high=29.978587 close=29.978587 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000003 지노믹트리(228760) high=29.975227 close=29.975227 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000004 위드텍(348350) high=29.968944 close=29.968944 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000005 아이빔테크놀로지(460470) high=29.963009 close=29.963009 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000006 유유제약2우B(000227) high=29.92465 close=29.92465 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000007 레이저옵텍(199550) high=29.908257 close=12.66055 bucket=UPPER_LIMIT_TOUCHED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000008 셀리드(299660) high=29.90099 close=29.90099 bucket=UPPER_LIMIT_CLOSED audit=DIRECT_TICKER_HIT_FINAL
CEN-000009 코스텍시스(355150) high=29.90099 close=13.663366 bucket=UPPER_LIMIT_TOUCHED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000010 헝셩그룹(900270) high=29.844961 close=29.844961 bucket=UPPER_LIMIT_CLOSED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000011 알파녹스(043100) high=29.843986 close=2.918973 bucket=UPPER_LIMIT_TOUCHED audit=NEWSLESS_OR_UNEXPLAINED
CEN-000012 딥마인드(223310) high=29.791271 close=10.056926 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000013 브이티(018290) high=28.825623 close=27.046263 bucket=HIGH20 audit=DIRECT_TICKER_HIT_FINAL
CEN-000014 이수페타시스(007660) high=28.436019 close=26.777251 bucket=HIGH20 audit=SCREENED_OUT_BUT_WINNER
CEN-000015 삼아알미늄(006110) high=28.15405 close=12.084993 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000016 씨피시스템(413630) high=28.153153 close=10.135135 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000017 KC코트렐(119650) high=28.136882 close=19.771863 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000018 네오리진(094860) high=26.898445 close=14.638609 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000019 태림포장(011280) high=25.694094 close=7.016658 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000020 SBI인베스트먼트(019550) high=25.655172 close=21.241379 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000021 대동기어(008830) high=24.284666 close=12.032282 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000022 금호건설우(002995) high=22.997712 close=7.265446 bucket=HIGH20 audit=CANDIDATE_GENERATION_MISS
CEN-000023 메이슨캐피탈(021880) high=22.222222 close=9.318996 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000024 삐아(451250) high=22.093023 close=18.604651 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000025 에스유홀딩스(031860) high=21.387283 close=21.387283 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000026 유유제약(000220) high=21.058965 close=6.979543 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000027 지오릿에너지(270520) high=20.869565 close=15.594203 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000028 효성화학(298000) high=20.8 close=4.0 bucket=HIGH20 audit=RANKING_MISS
CEN-000029 엔켐(348370) high=20.629371 close=20.06993 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED
CEN-000030 아이엠티(451220) high=20.583942 close=3.649635 bucket=HIGH20 audit=NEWSLESS_OR_UNEXPLAINED

## 23. forecast scorecard
1. 알테오젠(196170) POSITIVE_BUT_NOT_LEADER high=8.774834 close=7.615894 rank=152 — 알테오젠, 다이이찌산쿄로부터 계약금 2000만달러 수령
2. 삼성SDI(006400) POSITIVE_BUT_NOT_LEADER high=3.474903 close=0.772201 rank=754 — 美, 삼성SDI·스텔란티스 합작법인에 10조5000억 대출 지원
3. LS ELECTRIC(010120) POSITIVE_BUT_NOT_LEADER high=7.914659 close=7.432897 rank=189 — 초고압 변압기 잭팟…LS일렉 5600억 계약
4. LG에너지솔루션(373220) POSITIVE_BUT_NOT_LEADER high=2.426564 close=1.404853 rank=1188 — LG엔솔, 美미시건 배터리 합작 공장의 GM 지분 인수(상보)
5. 대한항공(003490) MISS high=0.974659 close=0.0 rank=2052 — [IB토마토]대한항공, 아시아나 인수 확정…수익성 개선 본격화
6. 원준(382840) POSITIVE_BUT_NOT_LEADER high=3.078451 close=2.681231 rank=877 — 그로쓰리서치 "원준, 2차전지 넘어 신규 산업으로 확장 계획 중"
7. 현대건설(000720) POSITIVE_BUT_NOT_LEADER high=2.007299 close=1.459854 rank=1438 — 현대건설, 올해 도시정비사업 수주 6조원 돌파
8. 브이티(018290) HIT_HIGH20 high=28.825623 close=27.046263 rank=13 — 브이티, 이앤씨 지분 추가 인수…"화장품 시장 경쟁력 강화"
9. 진에어(272450) POSITIVE_BUT_NOT_LEADER high=1.257862 close=0.718778 rank=1876 — 진에어, 무안~오사카·나리타·타이베이 신규 운항
10. 한화오션(042660) POSITIVE_BUT_NOT_LEADER high=4.668675 close=3.915663 rank=471 — NH證 "한화오션, 美군함 MRO 외형 성장 확대… 목표주가 8% 상향"
11. LG전자(066570) POSITIVE_BUT_NOT_LEADER high=1.587302 close=0.907029 rank=1693 — "AI센터 발열 잡는다"… LG전자 '칠러' 차세대 수출 효자로
12. 딥노이드(315640) POSITIVE_BUT_NOT_LEADER high=2.373887 close=2.077151 rank=1222 — 딥노이드, RSNA서 초록 발표…AI 기반 폐 결절 진단 기술 주목
13. 노브랜드(145170) HIT_HIGH15 high=15.088757 close=11.242604 rank=62 — 노브랜드, 편안한 성장 속 아웃도어 인수 효과 기대-DS
14. 셀리드(299660) HIT_UPPER_LIMIT_CLOSED high=29.90099 close=29.90099 rank=8 — 셀리드, 코로나19 백신에 적용된 항원 플랫폼 기술 '한국 특허 등록' 결정
15. 카카오(035720) POSITIVE_BUT_NOT_LEADER high=2.016607 close=1.897983 rank=1434 — 카카오, 다음 뉴스서비스 신규 입점 공고
16. HD현대중공업(329180) POSITIVE_BUT_NOT_LEADER high=1.895735 close=0.947867 rank=1513 — NH證 “HD현대중공업, 수익성 더 좋아진다… 목표가 14% 상향”
17. 아이에스동서(010780) POSITIVE_BUT_NOT_LEADER high=2.283105 close=0.456621 rank=1279 — 아이에스동서, 에스케이에코플랜트와 661억원 규모 공급계약
18. 고려아연(010130) HIT_HIGH15 high=16.371368 close=9.284196 rank=54 — 고려아연, 장중 52주 최고가 150만원 돌파!
19. SK하이닉스(000660) POSITIVE_BUT_NOT_LEADER high=3.967254 close=3.84131 rank=614 — 김춘환 SK하이닉스 부사장 "요소기술 제때 개발하려면 실패 두려워 말아야"
20. 동아에스티(170900) POSITIVE_BUT_NOT_LEADER high=1.642036 close=0.656814 rank=1657 — 동아에스티 ‘그로트핀’ 전문약 매출 첫 1000억원 돌파

## 24. issuer-day 감독학습 모집단
postmortem_supervised_population issuer/final cases=20. brain_delta supervised_issuer_day_case=20.

## 25. 직접뉴스 event-level 감독학습 모집단
final_evidence_witness=20, supervised_direct_event_case=20; direct event sample weights are normalized to 1.0 per ticker-day.

## 26. 후보 생성·순위·event thesis 오류
rankable_nonfinal=47, candidate_ranking_error_case=3, ranking_error_case=2, candidate_generation_error_case=5.

## 27. 주도섹터 형성 연구 — sealed universe 기준
sealed universe는 BLIND 섹터 가설에서 유지했고, outcome 이후에 retro winner를 BLIND 적중으로 승격하지 않았다. 전력기기·바이오·배터리·방산·K뷰티·항공·AI센터 인프라를 theme_formation_case로 보존했다.

## 28. retrospective theme discovery
outcome_to_news audit class counts={'NEWSLESS_OR_UNEXPLAINED': 104, 'DIRECT_TICKER_HIT_FINAL': 4, 'SCREENED_OUT_BUT_WINNER': 1, 'CANDIDATE_GENERATION_MISS': 5, 'RANKING_MISS': 1}. outcome leader라도 CSV source chain이 없으면 newsless_or_unexplained로 둔다.

## 29. 수혜주 발견 연구
셀리드·브이티처럼 BLIND 직접 기사와 D 강한 outcome이 연결된 케이스는 beneficiary/direct-event record로 보존했다. 이수페타시스류는 뉴스는 있었지만 부정/유증 제동 성격이 강하므로 별도 error/negative-control로만 다룬다.

## 30. 대장 선택 correction·confirmation 연구
BLIND 상위가 모두 대장이 되지는 않았다. final 중 HIGH10 이상은 4개이며, final 밖 strong leader는 outcome_to_news_audit로 역추적했다.

## 31. 후보 실패·부정 대조군
negative_control_case=53. 탈락/감시 후보와 약한 outcome은 삭제하지 않고 브레이크 표본으로 보존했다.

## 32. 행·엔티티·ticker binding 오류
semantic_regression fixtures=7, passes=7, unexpected_fail=0. final forbidden quote role count=0.

## 33. 학습 적격성 매트릭스
brain_delta=341, training_eligible=230, closure_audit=341, underfilled=[].

## 34. Brain Delta 요약
brain_delta_counts_by_type={'supervised_issuer_day_case': 20, 'supervised_direct_event_case': 20, 'negative_control_case': 53, 'candidate_ranking_error_case': 3, 'newsless_or_unexplained_case': 104, 'beneficiary_discovery_case': 4, 'ranking_error_case': 2, 'candidate_generation_error_case': 5, 'blind_leader_preference_pair': 20, 'context_market_state_or_fact_case': 97, 'counterexample': 6, 'theme_formation_case': 7}; expected_min=332.

## 35. 다음 연구 질문
장전 뉴스의 직접 catalyst가 상한가 전체를 설명하지 못한 구간이 크다. 다음 연구는 newsless leader, rankable-but-cutline miss, 그리고 D-1 liquidity/context가 direct catalyst를 압도한 케이스를 분리해 학습해야 한다.

## 36. 출처·데이터 한계
뉴스 입력은 지정 CSV 1개와 sealed P snapshot, outcome snapshot은 stock-web D CSV Raw bytes에 한정했다. outcome 이후 발견한 이유는 BLIND 적중으로 세탁하지 않았다.