from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import news_scalping_lab.evaluation.quality_runtime as quality_runtime_module
from news_scalping_lab.contracts.quality_evaluation import (
    PairedPredictionManifest,
    PredictionSeal,
    quality_full_profile,
)
from news_scalping_lab.evaluation.quality_runtime import (
    _begin_quality_attempt,
    _begin_shared_preparation_attempt,
    _build_quality_attempt_ledger,
    _build_shared_preparation_ledger,
    _complete_quality_attempt,
    _complete_shared_preparation_attempt,
    _prediction_manifest_with_seal,
    _reconcile_immutable_seal_state,
    _validated_attempt_efficiency,
    _validated_shared_preparation_observation,
    _write_json_atomic,
)
from news_scalping_lab.utils import KST, write_json


def _seal(
    *,
    variant_id: str = "V0",
    prediction_sha256: str = "8" * 64,
    efficiency: dict[str, object] | None = None,
) -> PredictionSeal:
    observed_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    return PredictionSeal.model_validate(
        {
            "case_id": "CASE-1",
            "variant_id": variant_id,
            "variant_architecture_sha256": (
                "0" if variant_id == "V0" else "1"
            )
            * 64,
            "sealed_at": observed_at,
            "cutoff_at": observed_at,
            "blind_input_manifest": {
                "artifact_path": "blind/input.json",
                "sha256": "2" * 64,
            },
            "news_sha256": "3" * 64,
            "parsed_news_root_sha256": "4" * 64,
            "shared_context_sha256": "5" * 64,
            "brain_manifest": {
                "artifact_path": "brain/manifest.json",
                "sha256": "6" * 64,
            },
            "coverage_manifest": {
                "artifact_path": "brain/coverage.json",
                "sha256": "7" * 64,
            },
            "memory_snapshot_id": "MEMIDX-test",
            "d_minus_one_context": {
                "artifact_path": "blind/d-minus-one.json",
                "sha256": "a" * 64,
            },
            "d_minus_one_context_sha256": "a" * 64,
            "d_minus_one_candidate_universe_root_sha256": "b" * 64,
            "d_minus_one_snapshot_root_sha256": "c" * 64,
            "d_minus_one_source_revision_sha256": "f" * 64,
            "d_minus_one_snapshot_session_date": "2030-01-09",
            "d_minus_one_payload_sha256": "d" * 64,
            "d_minus_one_consumed_payload_sha256": "d" * 64,
            "d_minus_one_projection_policy": (
                "ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"
            ),
            "d_minus_one_projection_root_sha256": "0" * 64,
            "d_minus_one_projection_requested_ticker_count": 0,
            "d_minus_one_projection_snapshot_count": 0,
            "d_minus_one_projection_missing_ticker_count": 0,
            "candidate_universe_policy_sha256": "e" * 64,
            "prediction": {
                "artifact_path": f"predictions/{variant_id}.json",
                "sha256": prediction_sha256,
            },
            "context_manifest": {
                "artifact_path": f"manifests/{variant_id}.json",
                "sha256": "9" * 64,
            },
            "final_citation_count": 0,
            "efficiency": efficiency or {},
        }
    )


def _manifest(*, seals: list[PredictionSeal]) -> PairedPredictionManifest:
    observed = {seal.variant_id for seal in seals}
    paired = ["CASE-1"] if observed == {"V0", "V1"} else []
    return PairedPredictionManifest(
        run_id="QPRED-recovery",
        profile=quality_full_profile(
            provider="codex-oauth",
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        ),
        blind_selection={
            "artifact_path": "blind/selection.json",
            "sha256": "1" * 64,
        },
        expected_case_ids=["CASE-1"],
        expected_variant_architecture_sha256={
            "V0": "0" * 64,
            "V1": "1" * 64,
        },
        seals=seals,
        paired_case_ids=paired,
        all_predictions_sealed=bool(paired),
    )


def test_atomic_progress_replace_updates_complete_json(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"

    _write_json_atomic(path, {"revision": 1})
    _write_json_atomic(path, {"revision": 2, "sealed": True})

    assert path.read_text(encoding="utf-8").endswith("\n")
    assert '"revision": 2' in path.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_orphan_seal_is_recovered_but_conflict_and_missing_file_fail(
    tmp_path: Path,
) -> None:
    seal = _seal()
    seal_path = tmp_path / "seals" / "CASE-1" / "V0.json"
    write_json(seal_path, seal.model_dump(mode="json"))
    empty = _manifest(seals=[])

    observed, recovered = _reconcile_immutable_seal_state(
        empty,
        seal_path=seal_path,
        case_id="CASE-1",
        variant_id="V0",
    )
    assert observed == seal
    assert recovered is True
    progressed = _prediction_manifest_with_seal(empty, seal=seal)

    write_json(
        seal_path,
        _seal(prediction_sha256="f" * 64).model_dump(mode="json"),
    )
    with pytest.raises(ValueError, match="progress and immutable seal differ"):
        _reconcile_immutable_seal_state(
            progressed,
            seal_path=seal_path,
            case_id="CASE-1",
            variant_id="V0",
        )
    seal_path.unlink()
    with pytest.raises(ValueError, match="missing immutable seal"):
        _reconcile_immutable_seal_state(
            progressed,
            seal_path=seal_path,
            case_id="CASE-1",
            variant_id="V0",
        )


def test_attempt_ledger_recovers_interrupted_trace_and_accumulates_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter(
        [
            datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            datetime(2030, 1, 10, 8, 0, 10, tzinfo=KST),
            datetime(2030, 1, 10, 8, 0, 14, tzinfo=KST),
        ]
    )
    monkeypatch.setattr(quality_runtime_module, "now_kst", lambda: next(clock))
    output_dir = tmp_path / "predictions" / "QPRED-recovery"
    empty_before = {
        "trace_files": set(),
        "captured_at": "2030-01-10T08:00:00+09:00",
        "process_memory": None,
    }
    first = _begin_quality_attempt(
        tmp_path,
        output_dir=output_dir,
        run_id="QPRED-recovery",
        case_id="CASE-1",
        variant_id="V0",
        runtime_before=empty_before,
    )
    trace_path = tmp_path / "runs" / "traces" / "interrupted.json"
    write_json(
        trace_path,
        {
            "operation": "generate_structured",
            "status": "ok",
            "started_at": (first.started_at + timedelta(seconds=1)).isoformat(),
            "completed_at": (first.started_at + timedelta(seconds=3)).isoformat(),
            "retries": 1,
            "token_usage": {
                "prompt_tokens_estimate": 11,
                "completion_tokens_estimate": 5,
            },
        },
    )
    second = _begin_quality_attempt(
        tmp_path,
        output_dir=output_dir,
        run_id="QPRED-recovery",
        case_id="CASE-1",
        variant_id="V0",
        runtime_before={
            **empty_before,
            "trace_files": {trace_path.resolve().as_posix()},
        },
    )
    _complete_quality_attempt(
        second,
        elapsed_seconds=4.0,
        runtime_metrics={
            "logical_llm_call_count": 2,
            "oauth_live_agent_call_count": 1,
            "llm_checkpoint_hit_count": 1,
            "llm_prompt_tokens_estimate": 20,
            "llm_completion_tokens_estimate": 7,
        },
    )

    ledger, reference = _build_quality_attempt_ledger(
        tmp_path,
        output_dir=output_dir,
        run_id="QPRED-recovery",
        case_id="CASE-1",
        variant_id="V0",
        current_attempt=second,
    )

    assert ledger["attempt_count"] == 2
    assert ledger["recovered_interrupted_attempt_count"] == 1
    assert ledger["elapsed_accounting_status"] == "BOUNDED_RECOVERY"
    assert ledger["elapsed_exact_completed_seconds"] == 4.0
    assert ledger["elapsed_lower_bound_seconds"] == 7.0
    assert ledger["elapsed_upper_bound_seconds"] == 14.0
    assert ledger["contains_recovered_attempts"] is True
    assert ledger["runtime_metrics"]["logical_llm_call_count"] == 3
    assert ledger["runtime_metrics"]["oauth_live_agent_call_count"] == 3
    assert ledger["runtime_metric_statuses"]["logical_llm_call_count"] == (
        "LOWER_BOUND"
    )
    assert ledger["runtime_metrics"]["embedding_query_count"] is None
    assert ledger["runtime_metric_statuses"]["embedding_query_count"] == (
        "UNAVAILABLE"
    )
    efficiency = {
        "elapsed_accounting_status": ledger["elapsed_accounting_status"],
        "elapsed_exact_completed_seconds": ledger[
            "elapsed_exact_completed_seconds"
        ],
        "elapsed_lower_bound_seconds": ledger["elapsed_lower_bound_seconds"],
        "elapsed_upper_bound_seconds": ledger["elapsed_upper_bound_seconds"],
        "contains_recovered_attempts": ledger["contains_recovered_attempts"],
        "runtime_metrics": ledger["runtime_metrics"],
        "runtime_metric_statuses": ledger["runtime_metric_statuses"],
        "runtime_metrics_accounting_status": ledger[
            "runtime_metrics_accounting_status"
        ],
        "attempt_count": ledger["attempt_count"],
        "recovered_interrupted_attempt_count": 1,
        "attempt_ledger_artifact_path": reference.artifact_path,
        "attempt_ledger_sha256": reference.sha256,
    }
    typed = _validated_attempt_efficiency(
        tmp_path,
        seal=_seal(efficiency=efficiency),
        expected_prediction_run_id="QPRED-recovery",
    )
    assert typed.attempt_count == 2
    assert typed.runtime_metrics["logical_llm_call_count"] == 3


def test_shared_preparation_ledger_accumulates_interrupted_and_cache_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter(
        [
            datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            datetime(2030, 1, 10, 8, 0, 10, tzinfo=KST),
            datetime(2030, 1, 10, 8, 0, 12, tzinfo=KST),
        ]
    )
    monkeypatch.setattr(quality_runtime_module, "now_kst", lambda: next(clock))
    empty_before = {
        "trace_files": set(),
        "captured_at": "2030-01-10T08:00:00+09:00",
        "process_memory": None,
    }
    first = _begin_shared_preparation_attempt(
        tmp_path,
        scope_id="QSHARED-recovery",
        case_id="CASE-1",
        runtime_before=empty_before,
    )
    trace_path = tmp_path / "runs" / "traces" / "shared-interrupted.json"
    write_json(
        trace_path,
        {
            "operation": "generate_structured",
            "status": "ok",
            "started_at": (first.started_at + timedelta(seconds=1)).isoformat(),
            "completed_at": (first.started_at + timedelta(seconds=3)).isoformat(),
            "retries": 0,
            "token_usage": {
                "prompt_tokens_estimate": 13,
                "completion_tokens_estimate": 4,
            },
        },
    )
    second = _begin_shared_preparation_attempt(
        tmp_path,
        scope_id="QSHARED-recovery",
        case_id="CASE-1",
        runtime_before={
            **empty_before,
            "trace_files": {trace_path.resolve().as_posix()},
        },
    )
    _complete_shared_preparation_attempt(
        second,
        elapsed_seconds=2.0,
        runtime_metrics={
            "logical_llm_call_count": 0,
            "oauth_live_agent_call_count": 0,
            "llm_checkpoint_hit_count": 0,
            "llm_prompt_tokens_estimate": 0,
            "llm_completion_tokens_estimate": 0,
        },
        cache_hit=True,
        shared_context_sha256="a" * 64,
        shared_manifest_sha256="b" * 64,
    )
    ledger, reference = _build_shared_preparation_ledger(
        tmp_path,
        run_id="QPRED-shared-recovery",
        case_id="CASE-1",
        current_attempt=second,
        shared_context_sha256="a" * 64,
        shared_manifest_sha256="b" * 64,
    )

    assert ledger["attempt_count"] == 2
    assert ledger["build_attempt_count"] == 0
    assert ledger["cache_load_attempt_count"] == 1
    assert ledger["recovered_interrupted_attempt_count"] == 1
    assert ledger["elapsed_exact_completed_seconds"] == 2.0
    assert ledger["elapsed_lower_bound_seconds"] == 5.0
    assert ledger["elapsed_upper_bound_seconds"] == 12.0
    observed = _validated_shared_preparation_observation(
        tmp_path,
        reference=reference,
        expected_prediction_run_id="QPRED-shared-recovery",
        expected_case_id="CASE-1",
        expected_shared_context_sha256="a" * 64,
    )
    assert observed.contains_recovered_attempts is True
    assert observed.runtime_metric_statuses["embedding_query_count"] == (
        "UNAVAILABLE"
    )
