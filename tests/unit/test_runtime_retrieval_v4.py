from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    DominantSectorHypothesis,
)
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeRetrievalLane,
)
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.memory.daily_context import (
    DAILY_MEMORY_CONTEXT_MAX_BYTES,
    runtime_evidence_compact_payload,
)
from news_scalping_lab.memory.runtime_v4 import (
    RUNTIME_LANES,
    RuntimeCandidate,
    SemanticExposureState,
    build_runtime_evidence_memos,
    build_runtime_retrieval_trace,
    candidates_from_daily_artifacts,
    dynamic_runtime_budget,
    finalize_runtime_retrieval_trace,
    runtime_record_lanes,
    select_runtime_candidates,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.utils import KST, canonical_json, sha256_text

CUTOFF = datetime(2030, 1, 11, 8, 59, 59, tzinfo=KST)


def _record(
    record_id: str,
    *,
    record_type: str = "supervised_issuer_day_case",
    response_class: str = "POSITIVE",
    training_target: str = "issuer_day_response",
    payload_extra: dict[str, object] | None = None,
) -> BrainRecordEnvelope:
    payload: dict[str, object] = {
        "record_type": record_type,
        "response_class": response_class,
        "label_quality": "verified",
        "ticker": record_id[-6:],
        "company_name": "Generic issuer",
        "high_return_pct": 12.0 if response_class == "POSITIVE" else -2.0,
        **(payload_extra or {}),
    }
    digest = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="EP-20300110",
        trade_date=date(2030, 1, 10),
        available_from=datetime(2030, 1, 11, tzinfo=KST),
        training_target=training_target,
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        status="supported",
        confidence_label="high",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=digest,
        normalized_payload_sha256=digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=cast(dict[str, Any], payload),
    )


def _candidate(
    record: BrainRecordEnvelope,
    *,
    lanes: tuple[RuntimeRetrievalLane, ...] | None = None,
    score: float = 0.8,
    exposed: bool | None = None,
    rare: bool = False,
    ann_rank: int | None = 1,
    fts_rank: int | None = 1,
    replay_available_from: datetime | None = None,
) -> RuntimeCandidate:
    exposure = SemanticExposureState(
        payload_exposed=exposed,
        claim_referenced=False,
        rare_payload=rare,
    )
    return RuntimeCandidate(
        record=record,
        independent_unit_id=f"issuer-day:{record.record_id}",
        cell_ids=("CELL-A",),
        relevance_score=score,
        lanes=lanes or runtime_record_lanes(record, exposure=exposure),
        exposure=exposure,
        ann_rank=ann_rank,
        fts_rank=fts_rank,
        replay_available_from=replay_available_from,
    )


def _lane_candidates() -> list[RuntimeCandidate]:
    specs = [
        ("POSITIVE_ANALOG", _record("REC-POS")),
        (
            "NEGATIVE_CONTROL",
            _record("REC-NEG", record_type="negative_control_case", response_class="NEGATIVE"),
        ),
        ("NEAR_MISS", _record("REC-NEAR", response_class="NEAR_MISS")),
        ("COUNTEREXAMPLE", _record("REC-COUNT", record_type="counterexample", response_class="NEGATIVE")),
        (
            "NEWSLESS_OR_UNEXPLAINED",
            _record("REC-NEWSLESS", record_type="newsless_or_unexplained_case", response_class="UNEXPLAINED"),
        ),
        (
            "CANDIDATE_GENERATION_ERROR",
            _record("REC-GEN", record_type="candidate_generation_error_case", response_class="NEGATIVE"),
        ),
        (
            "CANDIDATE_RANKING_ERROR",
            _record("REC-RANK", record_type="candidate_ranking_error_case", response_class="NEGATIVE"),
        ),
        (
            "LEADER_SELECTION_PAIR",
            _record("REC-LEADER", record_type="blind_leader_preference_pair", response_class="NEGATIVE"),
        ),
        (
            "THEME_FORMATION_SUCCESS",
            _record("REC-THEME-P", record_type="supervised_theme_formation_case"),
        ),
        (
            "THEME_FORMATION_FAILURE",
            _record("REC-THEME-N", record_type="theme_formation_case", response_class="NEGATIVE"),
        ),
        (
            "CONTINUATION_SUCCESS",
            _record("REC-CONT-P", payload_extra={"continuation_result": "held"}),
        ),
        (
            "CONTINUATION_FAILURE",
            _record("REC-CONT-N", response_class="NEGATIVE", payload_extra={"continuation_result": "failed"}),
        ),
        ("RARE_MECHANISM", _record("REC-RARE")),
    ]
    return [
        _candidate(
            record,
            lanes=(cast(RuntimeRetrievalLane, lane),),
            rare=lane == "RARE_MECHANISM",
            exposed=lane != "RARE_MECHANISM",
        )
        for lane, record in specs
    ]


def _build(tmp_path: Path, candidates: list[RuntimeCandidate]):
    return build_runtime_retrieval_trace(
        tmp_path,
        run_id="RUN-V4",
        cluster_id="CLUSTER-V4",
        query_text="current event mechanism",
        cutoff_at=CUTOFF,
        memory_snapshot_id="MEMIDX-TEST",
        candidates=candidates,
        source_population_manifests=[],
        source_representative_manifests=[],
    )


def test_hnsw_fts_union(tmp_path: Path) -> None:
    candidates = [
        _candidate(_record("REC-ANN"), ann_rank=1, fts_rank=None),
        _candidate(_record("REC-FTS"), ann_rank=None, fts_rank=1),
    ]
    result = _build(tmp_path, candidates)
    rows = {row.record_id: row for row in result.trace.rows}

    assert "ANN_CANDIDATE" in rows["REC-ANN"].stages
    assert "FTS_CANDIDATE" not in rows["REC-ANN"].stages
    assert "FTS_CANDIDATE" in rows["REC-FTS"].stages
    assert "ANN_CANDIDATE" not in rows["REC-FTS"].stages


def test_replay_effective_availability_is_preserved_in_trace(
    tmp_path: Path,
) -> None:
    physical_future = _record("REC-REPLAY").model_copy(update={"available_from": datetime(2030, 2, 1, tzinfo=KST)})
    replay_time = datetime(2030, 1, 11, tzinfo=KST)

    result = _build(
        tmp_path,
        [_candidate(physical_future, replay_available_from=replay_time)],
    )

    row = result.trace.rows[0]
    assert row.available_from == datetime(2030, 2, 1, tzinfo=KST)
    assert row.replay_available_from == replay_time


def test_lane_balancing() -> None:
    candidates = _lane_candidates()
    selected, _dropped = select_runtime_candidates(
        candidates,
        budget=dynamic_runtime_budget(candidates),
    )

    assert {lane for _candidate_row, lane in selected} == set(RUNTIME_LANES)


def test_offline_unexposed_record_can_be_recovered() -> None:
    exposed = _candidate(_record("REC-EXPOSED"), score=0.9, exposed=True)
    unexposed = _candidate(_record("REC-UNEXPOSED"), score=0.8, exposed=False)
    selected, _dropped = select_runtime_candidates(
        [exposed, unexposed],
        budget=dynamic_runtime_budget([exposed, unexposed]),
    )

    assert any(candidate.record.record_id == "REC-UNEXPOSED" for candidate, _lane in selected)


def test_offline_unexposed_population_member_can_be_recovered(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    population_dir = tmp_path / "population"
    population_dir.mkdir()
    population_manifest = population_dir / "manifest.json"
    member_path = population_dir / "members.jsonl"
    member_rows = [
        {
            "record_id": "REC-EXPOSED",
            "independent_unit_id": "UNIT-EXPOSED",
            "matched_cell_ids": ["CELL-A"],
            "memory_lanes": ["positive_analogs"],
            "record_type": "supervised_issuer_day_case",
            "evidence_polarity": "POSITIVE",
            "path_type": "SINGLE_EVENT",
            "sample_weight": 1.0,
        },
        {
            "record_id": "REC-UNEXPOSED",
            "independent_unit_id": "UNIT-UNEXPOSED",
            "matched_cell_ids": ["CELL-A"],
            "memory_lanes": ["positive_analogs"],
            "record_type": "supervised_issuer_day_case",
            "evidence_polarity": "POSITIVE",
            "path_type": "SINGLE_EVENT",
            "sample_weight": 1.0,
        },
    ]
    member_path.write_text(
        "".join(canonical_json(row) + "\n" for row in member_rows),
        encoding="utf-8",
    )
    population_manifest.write_text(
        canonical_json(
            {
                "cluster_id": "CLUSTER-V4",
                "member_records": {
                    "artifact_path": "population/members.jsonl",
                },
            }
        ),
        encoding="utf-8",
    )
    representative_manifest = population_dir / "representatives.json"
    representative_rows = population_dir / "representative_rows.jsonl"
    representative_rows.write_text(
        canonical_json({"record_id": "REC-EXPOSED", "selection_score": 0.9}) + "\n",
        encoding="utf-8",
    )
    representative_manifest.write_text(
        canonical_json(
            {
                "cluster_id": "CLUSTER-V4",
                "representative_records": {
                    "artifact_path": "population/representative_rows.jsonl",
                },
            }
        ),
        encoding="utf-8",
    )
    records = {
        "REC-EXPOSED": _record("REC-EXPOSED"),
        "REC-UNEXPOSED": _record("REC-UNEXPOSED"),
    }
    monkeypatch.setattr(
        "news_scalping_lab.memory.runtime_v4._load_records_by_ids",
        lambda _root, record_ids: {record_id: records[record_id] for record_id in record_ids},
    )

    candidates = candidates_from_daily_artifacts(
        tmp_path,
        cluster_id="CLUSTER-V4",
        population_manifest_paths=[population_manifest],
        representative_manifest_paths=[representative_manifest],
        exposure_resolver=lambda record_id: SemanticExposureState(
            payload_exposed=record_id == "REC-EXPOSED",
            claim_referenced=False,
        ),
        ann_rank_by_cell={"CELL-A": 1},
    )

    assert {candidate.record.record_id for candidate in candidates} == {
        "REC-EXPOSED",
        "REC-UNEXPOSED",
    }


def test_rare_reasoning_record_is_not_suppressed() -> None:
    candidates = _lane_candidates()
    selected, _dropped = select_runtime_candidates(
        candidates,
        budget=dynamic_runtime_budget(candidates),
    )

    assert any(lane == "RARE_MECHANISM" for _candidate_row, lane in selected)


@pytest.mark.asyncio
async def test_selected_record_enters_runtime_evidence_memo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = _lane_candidates()
    retrieval = _build(tmp_path, candidates)
    records = {candidate.record.record_id: candidate.record for candidate in candidates}
    monkeypatch.setattr(
        "news_scalping_lab.memory.runtime_v4._load_records_by_ids",
        lambda _root, record_ids: {record_id: records[record_id] for record_id in record_ids},
    )

    class _Index:
        def representative_source_records(self, record_ids: list[str], *, cutoff_at: datetime):
            rows = [type("Source", (), {"record_id": record_id})() for record_id in record_ids]
            return object(), rows

    evidence = await build_runtime_evidence_memos(
        tmp_path,
        retrieval=retrieval,
        memory_index=cast(Any, _Index()),
        llm=DeterministicMockLLMProvider(),
    )
    memo_records = {record_id for memo in evidence.memos for record_id in memo.source_record_ids}

    assert memo_records == set(retrieval.selected_record_ids)
    assert all(
        row.runtime_payload_exposed and row.evidence_memo_ids
        for row in evidence.trace.rows
        if "LANE_SELECTED" in row.stages
    )


@pytest.mark.asyncio
async def test_final_memory_claim_has_record_citation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate_row = _candidate(_record("REC-CITED"), exposed=False)
    retrieval = _build(tmp_path, [candidate_row])
    monkeypatch.setattr(
        "news_scalping_lab.memory.runtime_v4._load_records_by_ids",
        lambda _root, _ids: {"REC-CITED": candidate_row.record},
    )

    class _Index:
        def representative_source_records(self, record_ids: list[str], *, cutoff_at: datetime):
            return object(), [type("Source", (), {"record_id": value})() for value in record_ids]

    evidence = await build_runtime_evidence_memos(
        tmp_path,
        retrieval=retrieval,
        memory_index=cast(Any, _Index()),
        llm=DeterministicMockLLMProvider(),
    )
    prediction = BlindPrediction(
        prediction_id="PRED-V4",
        trade_date=date(2030, 1, 11),
        cutoff_at=CUTOFF,
        created_at=CUTOFF,
        blind_analysis=BlindAnalysis(summary="current evidence"),
        dominant_sectors=[
            DominantSectorHypothesis(
                name="Open-world sector",
                formation_mechanism="current news to issuer response",
                expected_breadth="uncertain",
                supporting_record_ids=["REC-CITED"],
            )
        ],
        candidates=[
            Candidate(
                rank=1,
                ticker="000001",
                company_name="Generic issuer",
                path_type="SINGLE_EVENT",
                thesis="current evidence",
                why_now="current news",
                memory_record_ids=["REC-CITED"],
            )
        ],
    )
    final_trace, _path = finalize_runtime_retrieval_trace(
        tmp_path,
        evidence=evidence,
        prediction=prediction,
    )

    assert final_trace.rows[0].final_candidate_ids
    assert final_trace.rows[0].final_sector_ids
    assert "FINAL_CITED" in final_trace.rows[0].stages


def test_online_full_scan_zero(tmp_path: Path) -> None:
    result = _build(tmp_path, _lane_candidates())

    assert result.trace.online_full_scan_count == 0


def test_dynamic_budget_respects_hard_limits() -> None:
    candidates = [
        _candidate(
            _record(f"REC-{index:03d}"),
            score=1.0 - index / 1000,
            exposed=index % 2 == 0,
            rare=index % 7 == 0,
        )
        for index in range(200)
    ]
    budget = dynamic_runtime_budget(candidates)
    selected, _dropped = select_runtime_candidates(candidates, budget=budget)

    assert 16 <= budget.initial_record_count <= 32
    assert budget.max_record_count <= 128
    assert budget.max_depth <= 3
    assert len(selected) <= budget.max_record_count


def test_runtime_evidence_compact_payload_stays_within_budget(
    tmp_path: Path,
) -> None:
    retrieval = _build(tmp_path, [_candidate(_record("REC-COMPACT"))])
    memo = RuntimeEvidenceMemo(
        memo_id="RMEMO-COMPACT",
        cluster_id=retrieval.trace.cluster_id,
        lane="POSITIVE_ANALOG",
        source_record_ids=["REC-COMPACT"],
        source_record_hash_root="a" * 64,
        current_vs_history_similarities=["bounded similarity"],
        current_vs_history_differences=["bounded difference"],
    )
    compact = {
        "representative_records": [
            {"record_id": f"REC-LEGACY-{index}", "context_excerpt": "x" * 1_600} for index in range(40)
        ],
        "category_brain_guidance": [],
        "omitted_counts": {
            "representative_records": 0,
            "category_brain_guidance": 0,
        },
    }

    result = runtime_evidence_compact_payload(
        compact,
        traces=[retrieval.trace],
        memos=[memo],
    )

    assert len(canonical_json(result).encode("utf-8")) <= (DAILY_MEMORY_CONTEXT_MAX_BYTES)
    assert result["representative_records"] == []
    assert result["runtime_evidence_memos"][0]["source_record_ids"] == ["REC-COMPACT"]
