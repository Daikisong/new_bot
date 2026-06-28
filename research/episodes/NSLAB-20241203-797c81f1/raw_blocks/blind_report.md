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