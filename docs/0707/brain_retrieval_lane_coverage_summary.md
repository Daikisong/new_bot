# 2026-07-07 Brain Retrieval Lane Coverage Summary

## 목적

오늘 작업의 핵심은 오늘 뉴스 cluster가 과거 두뇌 record와 매칭될 때, 단순히 semantic distance 상위 몇 개만 보는 구조를 피하는 것이다. 단일 top-K 검색은 긍정 사례나 자주 등장한 표현으로 쏠릴 수 있고, 반례, 실패 사례, near miss, leader 선택 실패 같은 학습 신호를 놓칠 수 있다.

따라서 검색 대상은 전체 brain으로 열어두되, final synthesis context에 올리는 record는 lane별로 균형 있게 제한한다.

## 기존 위험

기존 설계상 cluster coverage가 단일 retrieval query 또는 단일 top-K처럼 해석될 여지가 있었다.

문제는 다음과 같다.

```text
event cluster
-> semantic search top 3
-> 가장 가까운 record만 final synthesis에 승격
```

이 구조는 top 3이 모두 긍정 사례일 수 있다. 그러면 분석기는 "비슷한 과거에는 올랐다"만 보고, "비슷했지만 실패한 경우", "테마는 있었지만 leader 선택이 틀린 경우", "후보 생성이 noisy했던 경우"를 충분히 못 본다.

## 변경된 계약

cluster coverage는 이제 단일 top-K가 아니라 lane-balanced retrieval이다.

기본 lane:

```text
positive_analogs
negative_controls
near_misses
counterexamples
leader_selection_pairs
theme_formation_failures
candidate_generation_errors
```

각 event cluster마다 위 lane을 모두 실행한다. 기본값은 lane별 최대 3개 record다.

```text
cluster 1개
-> 7개 lane query
-> lane별 최대 3개 record
-> cluster당 최대 21개 방향성 record 후보
```

여기서 3개 제한은 전체 brain 검색 제한이 아니다. 전체 brain에서 각 lane에 맞는 record를 찾고, final synthesis에 올릴 본문 수만 제한한다.

## Promotion Limit 의미

`cluster_coverage_promoted_record_limit`은 전체 10년치 두뇌 사용을 막는 값이 아니다.

의미는 다음과 같다.

```text
raw artifact
  = 모든 cluster-lane query, raw ids, included ids, excluded ids를 감사용으로 보존

promoted records
  = final synthesis context에 실제 본문으로 올리는 제한된 record
```

기본 promotion limit은 360이다. 이 값은 final synthesis가 너무 큰 context를 받지 않게 하는 운영 상한이다. 검색과 감사는 artifact에 남고, 본문 승격만 제한된다.

## 구현 요약

변경된 주요 위치:

```text
src/news_scalping_lab/config.py
configs/default.yaml
src/news_scalping_lab/inference/analyzer.py
src/news_scalping_lab/contracts/models.py
src/news_scalping_lab/context/final_synthesis.py
src/news_scalping_lab/cli.py
src/news_scalping_lab/audits/provenance.py
src/news_scalping_lab/reporting/bundle.py
src/news_scalping_lab/reporting/render.py
schemas/*.schema.json
tests/unit/test_analysis_modes.py
tests/integration/test_analyze_e2e.py
tests/unit/test_cli.py
tests/unit/test_guards_and_audits.py
```

추가된 핵심 artifact:

```text
runs/checkpoints/semantic_retrieval/<RUN_ID>/semantic_cluster_coverage.jsonl
```

각 row는 cluster와 retrieval lane을 함께 기록한다.

```text
cluster_id
retrieval_lane
category
coverage_query
query_sha256
source_ids
raw_record_ids
included_record_ids
excluded_record_ids
record_retrieval_filters
cutoff_at
```

## 검증 계약

이제 다음 조건을 검증한다.

```text
semantic_cluster_coverage_query_count
  == event_cluster_count * cluster_coverage_lane_count

semantic_cluster_coverage_missing_ids
  == []

semantic_cluster_coverage_summary.cluster_coverage_lane_query_counts
  == lane별 query count

final_synthesis_context.payload.semantic_cluster_coverage
  contains rows, covered ids, missing ids, promoted records
```

candidate expansion도 event cluster coverage가 닫히도록 연결했다.

```text
candidate_expansion_cluster_coverage_ids
candidate_expansion_audit_only_cluster_ids
candidate_expansion_uncovered_cluster_ids
```

## 검증 결과

실행한 검증:

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest -q
```

결과:

```text
ruff 통과
mypy 통과
pytest 전체 통과
```

## 결론

오늘 변경으로 cluster coverage는 더 이상 "가까운 record 3개만 보는 구조"가 아니다.

이제 분석기는 각 뉴스 cluster에 대해 성공 사례, 실패 사례, near miss, 반례, leader 선택, 테마 형성 실패, 후보 생성 오류를 나눠서 과거 두뇌를 확인한다. 따라서 10년치 연구 record가 쌓였을 때도 단순 keyword gate나 긍정 사례 편향이 아니라, 다양한 과거 record를 균형 있게 참조하는 쪽으로 작동한다.
