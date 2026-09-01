# Daily LLM Call Graph Before Architecture Correction

감사 기준 commit은 `4c289eae23f1ee9d634c9c7ecf575d7899c2bec9`이다. 대상은 현재 CLI `nslab analyze`가 호출하는 `DailyAnalyzer.analyze()`이다.

## 결론

현재 정상적인 비어 있지 않은 입력의 논리 호출 수는 다음과 같다.

```text
B_open_world + B_novelty + P_runtime_evidence + 5
```

runtime evidence가 하나도 없어도 최소 7회다. production memory v4가 준비되어 있으면 `P_runtime_evidence`가 historical raw-record assignment 수와 prompt packing 결과에 따라 증가한다. 따라서 현재 경로는 목표인 정상 2회, schema repair 포함 최대 4회를 만족하지 않는다.

## 호출 지점

| 함수 | 목적 | 반복 차원 | 호출 공식 | historical raw |
|---|---|---|---|---|
| `_run_open_world_first_analysis` | current-day open-world 해석 | material cluster batch | `B_open_world` | 아니오 |
| `_run_news_novelty_review` | current-news novelty 검토 | cluster batch | `B_novelty` | 아니오 |
| `build_runtime_evidence_memos_packed` | 과거 근거 memo map | `(cluster, record, lane)` pack | `P_runtime_evidence` | 예 |
| `_run_semantic_retrieval_plan` | 검색 계획 | 없음 | 1 | 아니오 |
| `_run_candidate_expansion` | 후보 확장 | 없음 | 1 | 아니오 |
| `_generate_prediction` | 1차 예측 | 없음 | 1 | 아니오 |
| `run_red_team_pass` | 후보 반론 | 없음 | 1 | 아니오 |
| `_run_final_synthesis` | 최종 합성 | 없음 | 1 | 아니오 |

정확한 파일·라인과 입력 출처는 동반 JSON에 기록했다.

## 동적 증거

중단된 `QPRED-704f15cde6e4152b6931`의 첫 사례는 material cluster 478개, `(cluster_id, record_id, lane)` assignment 49,984개, 고유 historical record 1,560개를 만들었다. 중복 제거 뒤 payload occurrence는 1,914개였고 runtime evidence prompt plan은 379회였다.

이 실행은 5팩 뒤 중단됐고 전체 계보가 `HALTED_MISALIGNED_DIAGNOSTIC_ONLY`다. 재개·점수화·promotion은 금지한다. 이 수치는 품질 결과가 아니라 기존 daily 구조가 corpus 관계 수에 비례해 커진다는 동적 증거로만 사용한다.

## 교정 경계

새 production 명령 `analyze-daily`는 다음 두 호출만 허용해야 한다.

```text
CALL 1: CURRENT_DAY_INTERPRETATION
CALL 2: FINAL_MARKET_DECISION
```

historical raw record 해석, cluster별 LLM 호출, memory-cell별 LLM 호출, retrieval-lane별 LLM 호출은 새 경로에서 도달 불가능해야 한다. 기존 heavy 코드는 명시적 legacy/diagnostic 도구로만 남긴다.
