from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

import news_scalping_lab.evaluation.quality_runtime as quality_runtime_module
import news_scalping_lab.evaluation.shared_pre_retrieval as shared_pre_retrieval_module
import news_scalping_lab.inference.analyzer as analyzer_module
from news_scalping_lab.brain.compiler import BrainCompiler
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import ContextManifest, PriceSnapshot
from news_scalping_lab.contracts.quality_evaluation import (
    BlindRuntimeSelection,
    DMinusOnePromptProjection,
    PairedPredictionManifest,
    PredictionSeal,
    QualityArtifactReference,
    QualityEvaluationProfile,
    SharedDownstreamDigest,
    SharedPreRetrievalContext,
    quality_full_profile,
    quality_full_runtime_profile,
)
from news_scalping_lab.evaluation.quality_runtime import (
    load_blind_runtime_selection,
    materialize_blind_case_news,
    score_runtime_variants,
)
from news_scalping_lab.evaluation.quality_runtime import (
    prepare_quality_runtime_selection as _prepare_quality_runtime_selection,
)
from news_scalping_lab.evaluation.shadow import SHADOW_DAILY_P95_BUDGET_MS
from news_scalping_lab.evaluation.shared_pre_retrieval import (
    build_shared_pre_retrieval_context as _build_shared_pre_retrieval_context,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    sha256_text,
    write_json,
)


def prepare_quality_runtime_selection(
    root: Path,
    **kwargs: Any,
) -> quality_runtime_module.QualityRuntimeSelectionResult:
    kwargs.setdefault("price_source", _TestPriceSource())
    return _prepare_quality_runtime_selection(root, **kwargs)


async def build_shared_pre_retrieval_context(
    root: Path,
    **kwargs: Any,
) -> shared_pre_retrieval_module.SharedPreRetrievalBuildResult:
    close = float(kwargs.pop("_d1_close", 100.0))
    if "d_minus_one_context" not in kwargs:
        kwargs.update(
            _sealed_d_minus_one_kwargs(
                root,
                trade_date=kwargs["trade_date"],
                cutoff_at=kwargs["cutoff_at"],
                close=close,
            )
        )
    return await _build_shared_pre_retrieval_context(root, **kwargs)


def test_quality_full_has_no_latency_abort() -> None:
    profile = quality_full_profile(
        provider="codex-oauth",
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
    )

    assert profile.profile == "QUALITY_FULL"
    assert profile.wall_clock_limit_seconds is None
    assert profile.daily_p95_gate_seconds is None
    assert profile.latency_is_blocking is False
    assert profile.token_is_blocking is False
    assert profile.call_count_is_blocking is False
    assert profile.checkpoint_resume_required is True


def test_shared_preparation_ledger_preserves_superseded_completed_contexts(
    tmp_path: Path,
) -> None:
    runtime_before = {"trace_files": set()}
    first = quality_runtime_module._begin_shared_preparation_attempt(
        tmp_path,
        scope_id="QSHARED-test",
        case_id="CASE-test",
        runtime_before=runtime_before,
    )
    quality_runtime_module._complete_shared_preparation_attempt(
        first,
        elapsed_seconds=1.0,
        runtime_metrics={},
        cache_hit=False,
        shared_context_sha256="1" * 64,
        shared_manifest_sha256="2" * 64,
    )
    current = quality_runtime_module._begin_shared_preparation_attempt(
        tmp_path,
        scope_id="QSHARED-test",
        case_id="CASE-test",
        runtime_before=runtime_before,
    )
    quality_runtime_module._complete_shared_preparation_attempt(
        current,
        elapsed_seconds=2.0,
        runtime_metrics={},
        cache_hit=True,
        shared_context_sha256="3" * 64,
        shared_manifest_sha256="4" * 64,
    )

    ledger, _reference = quality_runtime_module._build_shared_preparation_ledger(
        tmp_path,
        run_id="QPRED-test",
        case_id="CASE-test",
        current_attempt=current,
        shared_context_sha256="3" * 64,
        shared_manifest_sha256="4" * 64,
    )

    assert [row["context_status"] for row in ledger["attempts"]] == [
        "SUPERSEDED",
        "CURRENT",
    ]
    assert ledger["attempts"][0]["shared_context_sha256"] == "1" * 64
    assert ledger["attempts"][1]["shared_context_sha256"] == "3" * 64
    quality_runtime_module._validate_attempt_ledger_aggregates(
        tmp_path,
        ledger,
        shared=True,
    )


def test_existing_phase8_budget_does_not_block_quality_full() -> None:
    assert SHADOW_DAILY_P95_BUDGET_MS == 90_000

    profile = quality_full_profile(
        provider="codex-oauth",
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
    )

    dumped = profile.model_dump(mode="json")
    assert 90 not in dumped.values()
    assert 90_000 not in dumped.values()
    assert dumped["daily_p95_gate_seconds"] is None
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = ContextManifest(
        run_id="RUN-quality-full-budget",
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        as_of=cutoff,
        news_window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        news_window_end_at=cutoff,
        accepted_episode_count=0,
        swept_episode_count=0,
        llm_model_config={"evaluation_profile": "QUALITY_FULL"},
        price_snapshot=PriceSnapshot(
            source_name="mock",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )
    assert DailyAnalyzer._final_synthesis_token_budget_is_blocking(manifest) is False


@pytest.mark.parametrize(
    ("provider", "model", "reasoning_effort"),
    [
        ("mock", "gpt-5.6-sol", "xhigh"),
        ("codex-oauth", "gpt-5.4", "xhigh"),
        ("codex-oauth", "gpt-5.6-sol", "high"),
    ],
)
def test_quality_full_runtime_identity_is_fail_closed(
    provider: str,
    model: str,
    reasoning_effort: str,
) -> None:
    with pytest.raises(ValueError, match="formal QUALITY_FULL runtime requires"):
        quality_full_runtime_profile(
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort,
        )

    exact = quality_full_runtime_profile(
        provider="codex-oauth",
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
    )
    assert (exact.provider, exact.model, exact.reasoning_effort) == (
        "codex-oauth",
        "gpt-5.6-sol",
        "xhigh",
    )


def test_live_operational_model_identity_remains_configurable() -> None:
    profile = QualityEvaluationProfile(
        profile="LIVE_OPERATIONAL",
        provider="user-provider",
        model="user-model",
        reasoning_effort="user-effort",
        wall_clock_limit_seconds=12.5,
        daily_p95_gate_seconds=10.0,
        latency_is_blocking=True,
        live_latency_target_source="USER_DEFINED_ONLY",
    )

    assert profile.profile == "LIVE_OPERATIONAL"
    assert profile.provider == "user-provider"
    assert profile.model == "user-model"


def test_prediction_process_cannot_resolve_truth(tmp_path: Path) -> None:
    path = (
        tmp_path
        / quality_runtime_module.BLIND_SELECTION_ROOT
        / ("QSEL-" + "a" * 20)
        / quality_runtime_module.BLIND_SELECTION_FILENAME
    )
    payload = _blind_selection_payload(tmp_path)
    payload["cases"][0]["outcome_ledger"] = {
        "artifact_path": "truth.jsonl",
        "sha256": "a" * 64,
    }
    write_json(path, payload)

    with pytest.raises(ValueError, match="forbidden outcome fields"):
        load_blind_runtime_selection(tmp_path, path)


@pytest.mark.parametrize(
    "untrusted_path",
    [
        Path("research/truth-do-not-touch/outcome_ledger.jsonl"),
        quality_runtime_module.BLIND_SELECTION_ROOT / ("QSEL-" + "b" * 20) / "runtime_outcome_selection.json",
    ],
)
def test_blind_selection_allowlist_rejects_before_touching_untrusted_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    untrusted_path: Path,
) -> None:
    candidate = tmp_path / untrusted_path
    original_resolve = Path.resolve
    original_stat = Path.stat

    def is_untrusted(path: Path) -> bool:
        return "truth-do-not-touch" in path.parts or path.name == "runtime_outcome_selection.json"

    def guarded_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if is_untrusted(path):
            raise AssertionError("prediction attempted to resolve an untrusted path")
        return original_resolve(path, *args, **kwargs)

    def guarded_stat(path: Path, *args: object, **kwargs: object) -> object:
        if is_untrusted(path):
            raise AssertionError("prediction attempted to stat an untrusted path")
        return original_stat(path, *args, **kwargs)

    def guarded_read_json(path: Path) -> object:
        if is_untrusted(path):
            raise AssertionError("prediction attempted to read an untrusted path")
        return {}

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    monkeypatch.setattr(Path, "stat", guarded_stat)
    monkeypatch.setattr(quality_runtime_module, "read_json", guarded_read_json)

    with pytest.raises(ValueError, match="blind selection path"):
        load_blind_runtime_selection(tmp_path, candidate)


def test_three_case_selection_uses_news_rows_without_opening_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = [
        _source_case(tmp_path, index=index, row_count=row_count)
        for index, row_count in enumerate((5, 1, 4, 3, 2), start=1)
    ]
    source_path = tmp_path / "source_selection.json"
    write_json(
        source_path,
        {
            "schema_version": "nslab.semantic_upgrade_split_selection.v1",
            "cases": cases,
        },
    )
    sealed_paths = {
        (tmp_path / reference["artifact_path"]).resolve()
        for case in cases
        for reference in (case["normalized_index"], case["source_ledger"])
    }
    sealed_buffers = {path: path.read_bytes() for path in sealed_paths}
    read_counts = dict.fromkeys(sealed_paths, 0)
    original_read_bytes = Path.read_bytes

    def swapping_preparation_bytes(candidate: Path) -> bytes:
        target = candidate.resolve()
        if target in sealed_buffers:
            read_counts[target] += 1
            if read_counts[target] > 1:
                return b'{"D_day_outcome":true}\n'
            return sealed_buffers[target]
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_preparation_bytes)

    result = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_path,
        split="CALIBRATION",
        scope="THREE_CASE",
    )

    assert [case.cutoff_safe_news_row_count for case in result.blind_selection.cases] == [
        1,
        3,
        5,
    ]
    blind_payload = result.blind_selection_path.read_text(encoding="utf-8")
    blind_json = json.loads(blind_payload)
    assert all("outcome_ledger" not in case for case in blind_json["cases"])
    assert all("normalized_index" not in case for case in blind_json["cases"])
    assert all("source_ledger" not in case for case in blind_json["cases"])
    assert all(
        case["blind_input_manifest"]["artifact_path"].startswith(
            "runs/semantic_brain_upgrade/quality_full/blind_inputs/QINPUT-"
        )
        for case in blind_json["cases"]
    )
    assert result.outcome_selection.available_to_prediction_process is False
    assert len(result.outcome_selection.cases) == 3
    assert BlindRuntimeSelection.model_validate_json(blind_payload).outcome_reference_count == 0
    assert read_counts == dict.fromkeys(sealed_paths, 1)

    blind_reference = quality_runtime_module.QualityArtifactReference(
        artifact_path=result.blind_selection_path.relative_to(tmp_path).as_posix(),
        sha256=file_sha256(result.blind_selection_path),
    )
    blind_bytes = result.blind_selection_path.read_bytes()
    blind_read_count = 0

    def swapping_blind_selection_bytes(candidate: Path) -> bytes:
        nonlocal blind_read_count
        if candidate.resolve() == result.blind_selection_path.resolve():
            blind_read_count += 1
            if blind_read_count > 1:
                return b'{"cases":[{"outcome_ledger":true}]}\n'
            return blind_bytes
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_blind_selection_bytes)
    verified_blind_payload = quality_runtime_module._read_verified_json_reference(
        blind_reference,
        result.blind_selection_path,
    )
    quality_runtime_module._validate_blind_runtime_selection_payload(
        result.blind_selection_path,
        verified_blind_payload,
    )
    assert blind_read_count == 1


def test_blind_selection_identity_rejects_case_swap(tmp_path: Path) -> None:
    source_path = tmp_path / "source_selection.json"
    write_json(
        source_path,
        {
            "schema_version": "nslab.semantic_upgrade_split_selection.v1",
            "cases": [_source_case(tmp_path, index=index, row_count=index) for index in range(1, 4)],
        },
    )
    prepared = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_path,
        split="CALIBRATION",
        scope="THREE_CASE",
    )
    payload = json.loads(prepared.blind_selection_path.read_bytes())
    payload["cases"][0], payload["cases"][1] = (
        payload["cases"][1],
        payload["cases"][0],
    )
    write_json(prepared.blind_selection_path, payload)

    with pytest.raises(ValueError, match="selection location is invalid"):
        load_blind_runtime_selection(tmp_path, prepared.blind_selection_path)


def test_price_source_revision_changes_qinput_and_selection_identity(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source_selection.json"
    write_json(
        source_path,
        {
            "schema_version": "nslab.semantic_upgrade_split_selection.v1",
            "cases": [_source_case(tmp_path, index=index, row_count=index) for index in range(1, 4)],
        },
    )
    first = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_path,
        split="CALIBRATION",
        scope="THREE_CASE",
        price_source=_TestPriceSource(revision="1" * 64),
    )
    changed = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_path,
        split="CALIBRATION",
        scope="THREE_CASE",
        price_source=_TestPriceSource(revision="2" * 64),
    )

    assert first.blind_selection.selection_id != changed.blind_selection.selection_id
    assert first.blind_selection.cases[0].blind_input_manifest != changed.blind_selection.cases[0].blind_input_manifest
    assert first.blind_selection.cases[0].d_minus_one_source_revision_sha256 == "1" * 64
    assert changed.blind_selection.cases[0].d_minus_one_source_revision_sha256 == "2" * 64


def test_materialized_blind_news_reads_only_sealed_allowlist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source_selection.json"
    write_json(
        source_path,
        {
            "schema_version": "nslab.semantic_upgrade_split_selection.v1",
            "cases": [_source_case(tmp_path, index=index, row_count=index) for index in range(1, 4)],
        },
    )
    prepared = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_path,
        split="CALIBRATION",
        scope="THREE_CASE",
    )
    case = prepared.blind_selection.cases[0]
    manifest_path = tmp_path / case.blind_input_manifest.artifact_path
    manifest_payload = json.loads(manifest_path.read_bytes())
    sealed_csv_path = tmp_path / manifest_payload["news_csv"]["artifact_path"]
    sealed_csv_bytes = sealed_csv_path.read_bytes()
    original_read_bytes = Path.read_bytes
    csv_read_count = 0

    def swapping_csv_bytes(candidate: Path) -> bytes:
        nonlocal csv_read_count
        if candidate.resolve() == sealed_csv_path.resolve():
            csv_read_count += 1
            if csv_read_count > 1:
                return b"date,time,title,body\r\n2099-01-01,09:00:00,future,outcome\r\n"
            return sealed_csv_bytes
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_csv_bytes)

    materialized = materialize_blind_case_news(
        tmp_path,
        case=case,
        output_dir=tmp_path / "runs" / "predictions" / "QPRED-test",
    )

    assert materialized.news_sha256 == case.news_sha256
    assert materialized.row_count == case.cutoff_safe_news_row_count
    assert materialized.news_csv_path.parent.name == "inputs"
    assert materialized.news_csv_path.read_bytes() == sealed_csv_bytes
    assert csv_read_count == 1
    receipt = json.loads(materialized.receipt_path.read_bytes())
    assert receipt["prediction_input_boundary_version"] == "SEALED_BLIND_INPUT.v3"
    assert receipt["blind_input_manifest_sha256"] == case.blind_input_manifest.sha256
    assert "normalized_index" not in receipt
    assert "source_ledger" not in receipt


def test_blind_input_allowlist_rejects_before_truth_path_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = tmp_path / "source_selection.json"
    write_json(
        source_path,
        {
            "schema_version": "nslab.semantic_upgrade_split_selection.v1",
            "cases": [_source_case(tmp_path, index=index, row_count=index) for index in range(1, 4)],
        },
    )
    prepared = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_path,
        split="CALIBRATION",
        scope="THREE_CASE",
    )
    case = prepared.blind_selection.cases[0].model_copy(
        update={
            "blind_input_manifest": QualityArtifactReference(
                artifact_path="research/truth-do-not-touch/normalized_index.json",
                sha256="d" * 64,
            )
        }
    )
    original_resolve = Path.resolve

    def guarded_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        if "truth-do-not-touch" in path.parts:
            raise AssertionError("prediction attempted to resolve a truth path")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)

    with pytest.raises(ValueError, match="outside the sealed allowlist"):
        materialize_blind_case_news(
            tmp_path,
            case=case,
            output_dir=tmp_path / "runs" / "predictions" / "QPRED-test",
        )


@pytest.mark.asyncio
async def test_shared_pre_retrieval_runs_once_per_case(tmp_path: Path) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )

    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    trace_count = len(list((tmp_path / "runs" / "traces").glob("*.json")))
    second = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        trusted_cache_context_sha256=first.manifest.context.sha256,
    )

    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.context_path.read_bytes() == first.context_path.read_bytes()
    assert "quality_full/blind_inputs/QINPUT-" in (first.context.d_minus_one_safe_context.artifact_path)
    assert not (first.context_path.parent / "d_minus_one_safe_context.json").exists()
    assert first.context.component_artifact_root_sha256 == (first.manifest.component_artifact_root_sha256)
    assert first.context.downstream_digest_payload_sha256 == (first.manifest.downstream_digest_payload_sha256)
    assert len(list((tmp_path / "runs" / "traces").glob("*.json"))) == trace_count


@pytest.mark.asyncio
async def test_preseal_shared_cache_reuses_authenticated_checkpoints(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    trace_count = len(list((tmp_path / "runs" / "traces").glob("*.json")))
    resumed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert first.cache_hit is False
    assert resumed.cache_hit is True
    assert len(list((tmp_path / "runs" / "traces").glob("*.json"))) == trace_count


@pytest.mark.asyncio
async def test_shared_cache_rejects_coordinated_component_reference_tamper(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    ledger_path = tmp_path / first.context.event_cluster_ledger.artifact_path
    ledger_path.write_bytes(ledger_path.read_bytes() + b"\n")
    context_payload = json.loads(first.context_path.read_bytes())
    context_payload["event_cluster_ledger"]["sha256"] = file_sha256(ledger_path)
    candidate = SharedPreRetrievalContext.model_validate(context_payload)
    revised_component_root = shared_pre_retrieval_module._component_artifact_root_sha256(
        references=(shared_pre_retrieval_module._named_context_references(candidate)),
        map_reduce_nodes=candidate.map_reduce_nodes,
    )
    context_payload["component_artifact_root_sha256"] = revised_component_root
    write_json(first.context_path, context_payload)
    manifest_payload = json.loads(first.manifest_path.read_bytes())
    manifest_payload["component_artifact_root_sha256"] = revised_component_root
    manifest_payload["context"]["sha256"] = file_sha256(first.context_path)
    write_json(first.manifest_path, manifest_payload)

    with pytest.raises(ValueError, match="external anchor|content identity drifted"):
        await build_shared_pre_retrieval_context(
            tmp_path,
            settings=settings,
            profile=profile,
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            trusted_cache_context_sha256=first.manifest.context.sha256,
        )


@pytest.mark.asyncio
async def test_fresh_clustering_rejects_fully_resealed_cached_payload(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    clustering_path = tmp_path / first.context.event_clustering_result.artifact_path
    clustering_payload = json.loads(clustering_path.read_bytes())
    clustering_payload["warnings"] = [
        *clustering_payload.get("warnings", []),
        "coordinated cached clustering forgery",
    ]
    write_json(clustering_path, clustering_payload)
    context_payload = json.loads(first.context_path.read_bytes())
    context_payload["event_clustering_result"]["sha256"] = file_sha256(clustering_path)
    forged_cluster_root = shared_pre_retrieval_module._input_cluster_root_sha256(clustering_payload)
    context_payload["input_cluster_root_sha256"] = forged_cluster_root
    forged_context_sha = _reseal_shared_package(
        first,
        context_payload=context_payload,
        input_cluster_root_sha256=forged_cluster_root,
    )

    with pytest.raises(ValueError, match="fresh deterministic clustering"):
        await build_shared_pre_retrieval_context(
            tmp_path,
            settings=settings,
            profile=profile,
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            trusted_cache_context_sha256=forged_context_sha,
        )


@pytest.mark.asyncio
async def test_provider_checkpoint_rejects_fully_resealed_output_forgery(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    root_node = next(node for node in first.context.map_reduce_nodes if node.node_id == first.context.root_node_id)
    root_path = tmp_path / root_node.output.artifact_path
    root_payload = json.loads(root_path.read_bytes())
    root_payload["mechanisms"] = ["forged provider mechanism"]
    write_json(root_path, root_payload)
    context_payload = json.loads(first.context_path.read_bytes())
    for node in context_payload["map_reduce_nodes"]:
        if node["node_id"] == root_node.node_id:
            node["output"]["sha256"] = file_sha256(root_path)
    digest_path = tmp_path / first.context.downstream_digest.artifact_path
    digest_payload = json.loads(digest_path.read_bytes())
    digest_payload["open_world_root"]["mechanisms"] = ["forged provider mechanism"]
    forged_digest = SharedDownstreamDigest.model_validate(digest_payload)
    write_json(digest_path, digest_payload)
    context_payload["downstream_digest"]["sha256"] = file_sha256(digest_path)
    context_payload["downstream_digest_payload_sha256"] = sha256_text(
        canonical_json(forged_digest.model_dump(mode="json"))
    )
    forged_context_sha = _reseal_shared_package(
        first,
        context_payload=context_payload,
        input_cluster_root_sha256=(first.context.input_cluster_root_sha256),
    )

    with pytest.raises(ValueError, match="provider checkpoint commitment"):
        await build_shared_pre_retrieval_context(
            tmp_path,
            settings=settings,
            profile=profile,
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            trusted_cache_context_sha256=forged_context_sha,
        )


@pytest.mark.asyncio
async def test_shared_cache_identity_changes_with_actual_prompt_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    original = shared_pre_retrieval_module._map_prompt

    def changed_map_prompt(
        *,
        node_id: str,
        clusters: list[object],
        cutoff_at: datetime,
    ) -> str:
        return "PROMPT-CONTENT-REVISION\n" + original(
            node_id=node_id,
            clusters=clusters,  # type: ignore[arg-type]
            cutoff_at=cutoff_at,
        )

    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "_map_prompt",
        changed_map_prompt,
    )
    changed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert changed.cache_hit is False
    assert changed.context.context_id != first.context.context_id
    assert changed.context.prompt_sha256_root != first.context.prompt_sha256_root
    assert changed.manifest.lookup_identity_sha256 != (first.manifest.lookup_identity_sha256)
    assert changed.manifest.identity_sha256 != first.manifest.identity_sha256


@pytest.mark.asyncio
async def test_shared_cache_identity_binds_input_cluster_root(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    original_news = news_path.read_text(encoding="utf-8-sig")
    news_path.write_text(
        original_news + '2030-01-10,08:30:00,"Additional event","Distinct new mechanism."\n',
        encoding="utf-8-sig",
    )
    changed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert changed.cache_hit is False
    assert changed.context.input_cluster_root_sha256 != (first.context.input_cluster_root_sha256)
    assert changed.manifest.input_cluster_root_sha256 == (changed.context.input_cluster_root_sha256)
    assert changed.manifest.identity_sha256 != first.manifest.identity_sha256


@pytest.mark.asyncio
async def test_shared_lookup_recomputes_and_binds_fresh_cluster_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    original_cluster_once = shared_pre_retrieval_module._cluster_once

    async def revised_cluster_once(*args: Any, **kwargs: Any) -> object:
        result = await original_cluster_once(*args, **kwargs)
        return replace(
            result,
            clustering_version=result.clustering_version + ".fresh-revision",
        )

    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "_cluster_once",
        revised_cluster_once,
    )
    changed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert changed.cache_hit is False
    assert changed.context.input_cluster_root_sha256 != (first.context.input_cluster_root_sha256)
    assert changed.context.context_id != first.context.context_id
    assert changed.manifest.lookup_identity_sha256 != (first.manifest.lookup_identity_sha256)


@pytest.mark.asyncio
async def test_shared_cache_identity_changes_with_d_minus_one_payload(
    tmp_path: Path,
) -> None:
    class RevisionedPriceSource:
        source_name = "revisioned-test"

        def __init__(self, close: float) -> None:
            self.close = close

        def get_blind_snapshot_universe(self, *, through: date) -> list[PriceRecord]:
            return [
                PriceRecord(
                    ticker="000001",
                    trade_date=through,
                    close=self.close,
                )
            ]

        def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
            return []

        def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
            return None

        def get_outcome(self, ticker: str, *, trade_date: date) -> object:
            raise AssertionError("shared D-1 builder must not request outcomes")

    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        _d1_close=100.0,
    )
    changed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        _d1_close=101.0,
    )

    assert first.cache_hit is False
    assert changed.cache_hit is False
    assert first.context.context_id != changed.context.context_id
    assert first.context.d_minus_one_safe_context.sha256 != (changed.context.d_minus_one_safe_context.sha256)
    assert first.manifest.identity_sha256 != changed.manifest.identity_sha256
    assert first.context.provider_checkpoint_commitment_count == (first.context.logical_llm_call_count)
    assert changed.context.provider_checkpoint_commitment_count == (changed.context.logical_llm_call_count)
    assert first.context.committed_prompt_tokens_estimate > 0
    assert changed.context.committed_completion_tokens_estimate > 0


@pytest.mark.asyncio
async def test_resume_does_not_repeat_completed_oauth_call(tmp_path: Path) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    first = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    commitments_before = (
        first.context.provider_checkpoint_commitment_count,
        first.context.committed_prompt_tokens_estimate,
        first.context.committed_completion_tokens_estimate,
    )

    resumed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        trusted_cache_context_sha256=first.manifest.context.sha256,
    )

    assert commitments_before[0] > 0
    assert resumed.cache_hit is True
    assert (
        resumed.context.provider_checkpoint_commitment_count,
        resumed.context.committed_prompt_tokens_estimate,
        resumed.context.committed_completion_tokens_estimate,
    ) == commitments_before
    context_payload = resumed.context.model_dump(mode="json")
    assert "live_llm_call_count" not in context_payload
    assert "checkpoint_hit_count" not in context_payload


@pytest.mark.asyncio
async def test_midbuild_resume_replays_completed_provider_checkpoints(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    original_novelty = shared_pre_retrieval_module._run_shared_novelty_review

    async def crash_before_novelty(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated crash after map/reduce checkpoints")

    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "_run_shared_novelty_review",
        crash_before_novelty,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        await build_shared_pre_retrieval_context(
            tmp_path,
            settings=settings,
            profile=profile,
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )
    checkpoint_dir = tmp_path / "runs" / "checkpoints" / "llm"
    completed = {
        path.name: path.read_bytes()
        for path in checkpoint_dir.glob("*.json")
        if "shared_open_world" in path.read_text(encoding="utf-8")
    }
    assert completed
    trace_baseline = {path.resolve() for path in (tmp_path / "runs" / "traces").glob("*.json")}
    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "_run_shared_novelty_review",
        original_novelty,
    )

    resumed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    new_traces = [
        json.loads(path.read_bytes())
        for path in (tmp_path / "runs" / "traces").glob("*.json")
        if path.resolve() not in trace_baseline
    ]

    assert resumed.cache_hit is False
    assert {
        path.name: path.read_bytes() for path in checkpoint_dir.glob("*.json") if path.name in completed
    } == completed
    assert any(
        str(trace.get("purpose", "")).startswith("shared_open_world") and trace.get("status") == "checkpoint_hit"
        for trace in new_traces
    )
    assert not any(
        str(trace.get("purpose", "")).startswith("shared_open_world") and trace.get("status") == "ok"
        for trace in new_traces
    )


@pytest.mark.asyncio
async def test_preseal_context_only_crash_resumes_without_live_oauth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort),
    )
    original_write = shared_pre_retrieval_module._write_immutable_json

    def crash_on_manifest(path: Path, payload: object) -> None:
        if path.name == "shared_pre_retrieval_context_manifest.json":
            raise RuntimeError("simulated crash before shared manifest")
        original_write(path, payload)

    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "_write_immutable_json",
        crash_on_manifest,
    )
    with pytest.raises(RuntimeError, match="before shared manifest"):
        await build_shared_pre_retrieval_context(
            tmp_path,
            settings=settings,
            profile=profile,
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )
    context_paths = list(
        (tmp_path / "runs" / "semantic_brain_upgrade" / "quality_full" / "shared_pre_retrieval").glob(
            "*/shared_pre_retrieval_context.json"
        )
    )
    assert len(context_paths) == 1
    context_bytes = context_paths[0].read_bytes()
    trace_baseline = {path.resolve() for path in (tmp_path / "runs" / "traces").glob("*.json")}
    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "_write_immutable_json",
        original_write,
    )

    resumed = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=profile,
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    new_traces = [
        json.loads(path.read_bytes())
        for path in (tmp_path / "runs" / "traces").glob("*.json")
        if path.resolve() not in trace_baseline
    ]

    assert resumed.cache_hit is False
    assert resumed.context_path.read_bytes() == context_bytes
    assert new_traces
    assert all(
        trace.get("status") == "checkpoint_hit"
        for trace in new_traces
        if str(trace.get("purpose", "")).startswith("shared_")
    )


@pytest.mark.asyncio
async def test_all_material_clusters_enter_shared_tree(tmp_path: Path) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    result = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    root = next(node for node in result.context.map_reduce_nodes if node.node_id == result.context.root_node_id)
    map_nodes = [node for node in result.context.map_reduce_nodes if node.kind == "MAP"]
    assert root.covered_cluster_ids == result.context.material_cluster_ids
    assert [
        cluster_id for node in map_nodes for cluster_id in node.covered_cluster_ids
    ] == result.context.material_cluster_ids
    assert result.manifest.complete_material_tree_coverage is True


@pytest.mark.asyncio
async def test_audit_only_clusters_stay_in_ledger_but_out_of_blind_novelty(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    original_news = news_path.read_text(encoding="utf-8-sig")
    repeated_audit_row = (
        '2030-01-09,15:00:00,"Old audit event",'
        '"Before the blind news window."\n'
    )
    news_path.write_text(
        original_news + repeated_audit_row + repeated_audit_row,
        encoding="utf-8-sig",
    )

    result = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    novelty_payload = json.loads(
        (tmp_path / result.context.news_novelty_review.artifact_path).read_bytes()
    )
    novelty_cluster_ids = [
        finding["cluster_id"] for finding in novelty_payload["findings"]
    ]
    ledger_rows = [
        json.loads(line)
        for line in (
            tmp_path / result.context.event_cluster_ledger.artifact_path
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(result.context.low_signal_cluster_ids) == 2
    assert len(set(result.context.low_signal_cluster_ids)) == 1
    assert novelty_cluster_ids == result.context.material_cluster_ids
    assert not set(novelty_cluster_ids) & set(result.context.low_signal_cluster_ids)
    assert sum(row["disposition"] == "AUDIT_ONLY" for row in ledger_rows) == 2


@pytest.mark.asyncio
async def test_no_first_n_or_silent_truncation(tmp_path: Path) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    result = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert result.context.first_n_shortcut_used is False
    assert result.context.silent_truncation_used is False
    cluster_ledger = (tmp_path / result.context.event_cluster_ledger.artifact_path).read_text(encoding="utf-8")
    assert '"first_n_shortcut_used":false' in cluster_ledger
    assert '"silent_truncation_used":false' in cluster_ledger
    digest_payload = json.loads((tmp_path / result.context.downstream_digest.artifact_path).read_bytes())
    assert digest_payload["material_cluster_ids"] == (result.context.material_cluster_ids)
    assert [
        finding["cluster_id"] for finding in digest_payload["novelty_findings"]
    ] == result.context.material_cluster_ids
    assert all(len(finding["omitted_payload_sha256"]) == 64 for finding in digest_payload["novelty_findings"])


def test_online_full_scan_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    analyzer = DailyAnalyzer(Settings(project_root=tmp_path))
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = ContextManifest(
        run_id="RUN-no-online-full-scan",
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        as_of=cutoff,
        news_window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
        news_window_end_at=cutoff,
        accepted_episode_count=0,
        swept_episode_count=0,
        available_record_ids=[f"REC-{index}" for index in range(1_000)],
        price_snapshot=PriceSnapshot(
            source_name="mock",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )

    def forbidden_record_open(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("online full corpus record payload scan")

    monkeypatch.setattr(BrainRecordStore, "get_record", forbidden_record_open)

    analyzer._refresh_counterexample_record_ids_from_retrieval(manifest)

    assert manifest.counterexample_record_ids == []


def test_prediction_normalization_is_resume_stable(tmp_path: Path) -> None:
    analyzer = DailyAnalyzer(Settings(project_root=tmp_path))
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    source = analyzer._make_prediction(
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        news_texts=["resume-stable catalyst"],
        event_ids=["EVT-resume"],
        retrieved_episode_ids=[],
        counterexample_episode_ids=[],
        retrieved_record_ids=[],
        counterexample_record_ids=[],
        excluded_source_ids=[],
        first_pass_mechanisms=["current event to candidate transmission"],
    )
    arguments = {
        "trade_date": date(2030, 1, 10),
        "cutoff_at": cutoff,
        "event_ids": ["EVT-resume"],
        "excluded_source_ids": [],
        "prompt": "sealed prompt",
        "purpose": "daily_blind_analysis",
    }

    first = analyzer._normalize_prediction(source, **arguments)
    second = analyzer._normalize_prediction(source, **arguments)

    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.created_at == cutoff
    assert all(
        provenance.observed_at == cutoff for candidate in first.candidates for provenance in candidate.provenance
    )


@pytest.mark.asyncio
async def test_v0_v1_consume_identical_shared_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    news_parse_count = 0
    original_news_loader = shared_pre_retrieval_module.load_news_csv

    def counted_news_loader(*args: object, **kwargs: object) -> object:
        nonlocal news_parse_count
        news_parse_count += 1
        return original_news_loader(*args, **kwargs)

    monkeypatch.setattr(
        shared_pre_retrieval_module,
        "load_news_csv",
        counted_news_loader,
    )
    monkeypatch.setattr(analyzer_module, "load_news_csv", counted_news_loader)
    shared = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    assert news_parse_count == 1

    async def forbidden_common_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("variant attempted to rerun a shared common stage")

    monkeypatch.setattr(
        DailyAnalyzer,
        "_run_open_world_first_analysis",
        forbidden_common_call,
    )
    monkeypatch.setattr(
        DailyAnalyzer,
        "_run_news_novelty_review",
        forbidden_common_call,
    )

    def forbidden_variant_d_minus_one(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("variant attempted candidate-dependent D-1 collection")

    monkeypatch.setattr(
        DailyAnalyzer,
        "_collect_d_minus_one_market_data",
        forbidden_variant_d_minus_one,
    )
    d_minus_one_path = tmp_path / shared.context.d_minus_one_safe_context.artifact_path
    d_minus_one_bytes = d_minus_one_path.read_bytes()
    analyses = []
    for variant in ("legacy", "v4"):
        analyses.append(
            await DailyAnalyzer(
                settings,
                runtime_retrieval_variant=variant,
            ).analyze(
                news_csv=news_path,
                trade_date=date(2030, 1, 10),
                cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
                mode="exhaustive",
                web_search=False,
                shared_pre_retrieval_context_path=shared.context_path,
                shared_pre_retrieval_context_sha256=(shared.manifest.context.sha256),
                shared_pre_retrieval_manifest_sha256=file_sha256(shared.manifest_path),
                sealed_blind_input_manifest_sha256="a" * 64,
                preloaded_news_batch=shared.news_batch,
                shared_d_minus_one_context_path=d_minus_one_path,
            )
        )

    manifests = [analysis.context_manifest for analysis in analyses]
    assert manifests[0].shared_pre_retrieval_context_sha256 == file_sha256(shared.context_path)
    assert manifests[0].shared_pre_retrieval_context_sha256 == (manifests[1].shared_pre_retrieval_context_sha256)
    assert manifests[0].shared_pre_retrieval_manifest_sha256 == (manifests[1].shared_pre_retrieval_manifest_sha256)
    assert manifests[0].open_world_first_analysis_artifact == (manifests[1].open_world_first_analysis_artifact)
    assert manifests[0].news_novelty_review_artifact == (manifests[1].news_novelty_review_artifact)
    assert all(
        manifest.d_minus_one_context_artifact == shared.context.d_minus_one_safe_context.artifact_path
        and manifest.d_minus_one_context_sha256 == shared.context.d_minus_one_safe_context.sha256
        for manifest in manifests
    )
    assert manifests[0].d_minus_one_candidate_universe_root_sha256 == (
        manifests[1].d_minus_one_candidate_universe_root_sha256
    )
    assert manifests[0].d_minus_one_snapshot_root_sha256 == (manifests[1].d_minus_one_snapshot_root_sha256)
    final_contexts = [
        json.loads((tmp_path / str(manifest.final_synthesis_context_artifact)).read_bytes()) for manifest in manifests
    ]
    expected_d_minus_one = json.loads(d_minus_one_bytes)
    projections = [
        DMinusOnePromptProjection.model_validate(context["payload"]["d_minus_one_market_data"])
        for context in final_contexts
    ]
    expected_full_payload_sha256 = sha256_text(canonical_json(expected_d_minus_one))
    for context, projection in zip(final_contexts, projections, strict=True):
        candidate_tickers = sorted(
            {
                str(candidate["ticker"]).strip().upper()
                for candidate in context["payload"]["candidate_research"]["candidates"]
                if str(candidate["ticker"]).strip().upper() not in {"", "UNKNOWN", "UNVERIFIED"}
            }
        )
        assert projection.full_context == shared.context.d_minus_one_safe_context
        assert projection.full_payload_sha256 == expected_full_payload_sha256
        assert projection.requested_tickers == candidate_tickers
        assert {row.ticker for row in projection.snapshots}.issubset(candidate_tickers)
    assert news_parse_count == 1
    assert all(
        manifest.prediction_input_boundary_version == "SEALED_BLIND_INPUT.v3"
        and manifest.sealed_blind_input_manifest_sha256 == "a" * 64
        for manifest in manifests
    )
    for manifest, final_context in zip(manifests, final_contexts, strict=True):
        manifest_payload = manifest.model_dump(mode="json")
        assert quality_runtime_module.final_synthesis_context_contract_verified(
            manifest_payload,
            final_context,
        )
        assert quality_runtime_module.final_synthesis_phase7_artifacts_compatible(
            tmp_path,
            manifest_payload,
            final_context,
        )

    final_path = tmp_path / str(manifests[0].final_synthesis_context_artifact)
    digest_path = tmp_path / shared.context.downstream_digest.artifact_path
    safe_buffers = {
        final_path.resolve(): final_path.read_bytes(),
        digest_path.resolve(): digest_path.read_bytes(),
    }
    read_counts = dict.fromkeys(safe_buffers, 0)
    original_read_bytes = Path.read_bytes

    def swapping_verified_bytes(candidate: Path) -> bytes:
        target = candidate.resolve()
        if target in safe_buffers:
            read_counts[target] += 1
            if read_counts[target] > 1:
                return b'{"coordinated_swap":{"D_day_outcome":true}}\n'
            return safe_buffers[target]
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_verified_bytes)
    verified_shared = quality_runtime_module._verify_final_synthesis_contract_and_shared_digest(
        root=tmp_path,
        context=manifests[0],
        shared_context=shared.context,
    )
    with pytest.raises(ValueError, match="generated candidate ticker is invalid"):
        quality_runtime_module._generated_candidate_tickers_from_final_context(
            verified_shared["verified_final_synthesis_artifact"]
        )
    assert read_counts == dict.fromkeys(safe_buffers, 1)
    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)

    changed_input = await DailyAnalyzer(
        settings,
        runtime_retrieval_variant="legacy",
    ).analyze(
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="exhaustive",
        web_search=False,
        shared_pre_retrieval_context_path=shared.context_path,
        shared_pre_retrieval_context_sha256=shared.manifest.context.sha256,
        shared_pre_retrieval_manifest_sha256=file_sha256(shared.manifest_path),
        sealed_blind_input_manifest_sha256="b" * 64,
        preloaded_news_batch=shared.news_batch,
        shared_d_minus_one_context_path=d_minus_one_path,
    )
    assert changed_input.run_id != analyses[0].run_id
    assert news_parse_count == 1

    tampered_manifest = manifests[0]
    final_context_path = tmp_path / str(tampered_manifest.final_synthesis_context_artifact)
    tampered_final_context = json.loads(final_context_path.read_bytes())
    tampered_final_context["payload"]["d_minus_one_market_data"]["source_name"] = "tampered-source"
    tampered_final_context["payload_sha256"] = sha256_text(canonical_json(tampered_final_context["payload"]))
    write_json(final_context_path, tampered_final_context)
    tampered_manifest.final_synthesis_context_sha256 = sha256_text(final_context_path.read_text(encoding="utf-8"))
    with pytest.raises(
        ValueError,
        match="projection root is invalid|different D-1 prompt projection",
    ):
        quality_runtime_module._verify_final_synthesis_d_minus_one_consumption(
            root=tmp_path,
            context=tampered_manifest,
            expected_context=quality_runtime_module.SharedDMinusOneContext.model_validate(expected_d_minus_one),
        )


@pytest.mark.asyncio
async def test_quality_full_shared_injection_is_all_or_none(tmp_path: Path) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    shared = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    with pytest.raises(ValueError, match="must be injected together"):
        await DailyAnalyzer(settings).analyze(
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            shared_pre_retrieval_context_path=shared.context_path,
        )


@pytest.mark.asyncio
async def test_preloaded_news_mutation_is_rejected_by_parsed_root(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    shared = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    shared.news_batch.items[0].title = "mutated after the single parse"
    d_minus_one_path = tmp_path / shared.context.d_minus_one_safe_context.artifact_path

    with pytest.raises(ValueError, match="parsed-news content identity drifted"):
        await DailyAnalyzer(settings).analyze(
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            shared_pre_retrieval_context_path=shared.context_path,
            shared_pre_retrieval_context_sha256=shared.manifest.context.sha256,
            shared_pre_retrieval_manifest_sha256=file_sha256(shared.manifest_path),
            sealed_blind_input_manifest_sha256="a" * 64,
            preloaded_news_batch=shared.news_batch,
            shared_d_minus_one_context_path=d_minus_one_path,
        )


def test_shared_d_minus_one_uses_fixed_universe_and_cutoff(tmp_path: Path) -> None:
    class FixedUniversePriceSource:
        source_name = "fixed-universe-test"

        def __init__(self) -> None:
            self.universe_calls: list[date] = []

        def get_blind_snapshot_universe(self, *, through: date) -> list[PriceRecord]:
            self.universe_calls.append(through)
            return [
                PriceRecord(
                    ticker="000001",
                    trade_date=date(2030, 1, 9),
                    close=100.0,
                ),
                PriceRecord(
                    ticker="000002",
                    trade_date=date(2030, 1, 8),
                    close=90.0,
                ),
            ]

        def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
            raise AssertionError("shared D-1 builder must not request history")

        def get_outcome(self, ticker: str, *, trade_date: date) -> object:
            raise AssertionError("shared D-1 builder must not request outcomes")

    source = FixedUniversePriceSource()
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)

    context = quality_runtime_module._build_privileged_d_minus_one_context(
        source,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
    )

    assert context.candidate_universe == ["000001"]
    assert [row.ticker for row in context.snapshots] == ["000001"]
    assert context.snapshot_session_date == date(2030, 1, 9)
    assert context.privileged_source_snapshot_count == 2
    assert context.price_repository_access_count == 0
    assert context.skipped_tickers == []
    assert source.universe_calls == [date(2030, 1, 9)]
    assert context.d_day_access_count == 0
    assert context.outcome_access_count == 0


def test_shared_d_minus_one_rejects_source_returning_d_day_snapshot(
    tmp_path: Path,
) -> None:
    class UnsafePriceSource:
        source_name = "unsafe-test"

        def get_blind_snapshot_universe(self, *, through: date) -> list[PriceRecord]:
            return [
                PriceRecord(
                    ticker="000001",
                    trade_date=date(2030, 1, 10),
                    close=100.0,
                )
            ]

        def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
            return []

        def get_outcome(self, ticker: str, *, trade_date: date) -> object:
            raise AssertionError("shared D-1 builder must not request outcomes")

    with pytest.raises(ValueError, match="D-day or future snapshot"):
        quality_runtime_module._build_privileged_d_minus_one_context(
            UnsafePriceSource(),
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )


@pytest.mark.asyncio
async def test_shared_d_minus_one_artifact_tamper_is_rejected(
    tmp_path: Path,
) -> None:
    settings, news_path = _shared_builder_project(tmp_path)
    shared = await build_shared_pre_retrieval_context(
        tmp_path,
        settings=settings,
        profile=quality_full_profile(
            provider=settings.llm_provider,
            model=settings.llm.model,
            reasoning_effort=str(settings.llm.reasoning_effort),
        ),
        news_csv=news_path,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    d_minus_one_path = tmp_path / shared.context.d_minus_one_safe_context.artifact_path
    original = json.loads(d_minus_one_path.read_bytes())
    original["source_name"] = "tampered-source"
    write_json(d_minus_one_path, original)

    with pytest.raises(ValueError, match="hash differs|component hash mismatch"):
        await DailyAnalyzer(settings).analyze(
            news_csv=news_path,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            shared_pre_retrieval_context_path=shared.context_path,
            shared_pre_retrieval_context_sha256=shared.manifest.context.sha256,
            shared_pre_retrieval_manifest_sha256=file_sha256(shared.manifest_path),
            sealed_blind_input_manifest_sha256="a" * 64,
            preloaded_news_batch=shared.news_batch,
            shared_d_minus_one_context_path=d_minus_one_path,
        )


def test_paired_prediction_rejects_d_minus_one_root_drift() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    common = {
        "case_id": "CASE-1",
        "sealed_at": cutoff,
        "cutoff_at": cutoff,
        "blind_input_manifest": {
            "artifact_path": "sealed/input.json",
            "sha256": "a" * 64,
        },
        "news_sha256": "b" * 64,
        "parsed_news_root_sha256": "0" * 64,
        "shared_context_sha256": "c" * 64,
        "brain_manifest": {
            "artifact_path": "brain/manifest.json",
            "sha256": "d" * 64,
        },
        "coverage_manifest": {
            "artifact_path": "brain/coverage.json",
            "sha256": "e" * 64,
        },
        "memory_snapshot_id": "MEMIDX-test",
        "d_minus_one_context": {
            "artifact_path": "shared/d-minus-one.json",
            "sha256": "f" * 64,
        },
        "d_minus_one_context_sha256": "f" * 64,
        "d_minus_one_candidate_universe_root_sha256": "1" * 64,
        "d_minus_one_snapshot_root_sha256": "2" * 64,
        "d_minus_one_source_revision_sha256": "0" * 64,
        "d_minus_one_snapshot_session_date": date(2030, 1, 9),
        "d_minus_one_payload_sha256": "a" * 64,
        "d_minus_one_consumed_payload_sha256": "a" * 64,
        "d_minus_one_projection_policy": ("ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"),
        "d_minus_one_projection_root_sha256": "b" * 64,
        "d_minus_one_projection_requested_ticker_count": 0,
        "d_minus_one_projection_snapshot_count": 0,
        "d_minus_one_projection_missing_ticker_count": 0,
        "candidate_universe_policy_sha256": "3" * 64,
        "final_citation_count": 0,
    }
    v0 = PredictionSeal(
        **common,
        variant_id="V0",
        variant_architecture_sha256="0" * 64,
        prediction={"artifact_path": "predictions/v0.json", "sha256": "4" * 64},
        context_manifest={"artifact_path": "manifests/v0.json", "sha256": "5" * 64},
    )
    v1_common = {
        **common,
        "d_minus_one_snapshot_root_sha256": "6" * 64,
    }
    v1 = PredictionSeal(
        **v1_common,
        variant_id="V1",
        variant_architecture_sha256="1" * 64,
        prediction={"artifact_path": "predictions/v1.json", "sha256": "7" * 64},
        context_manifest={"artifact_path": "manifests/v1.json", "sha256": "8" * 64},
    )

    with pytest.raises(ValueError, match="d_minus_one_snapshot_root_sha256 differs"):
        PairedPredictionManifest(
            run_id="QPRED-d1-parity",
            profile=quality_full_profile(
                provider="codex-oauth",
                model="gpt-5.6-sol",
                reasoning_effort="xhigh",
            ),
            blind_selection={
                "artifact_path": "selections/blind.json",
                "sha256": "9" * 64,
            },
            expected_case_ids=["CASE-1"],
            expected_variant_architecture_sha256={
                "V0": "0" * 64,
                "V1": "1" * 64,
            },
            seals=[v0, v1],
            paired_case_ids=["CASE-1"],
            all_predictions_sealed=True,
        )


def test_scoring_requires_both_prediction_seals(tmp_path: Path) -> None:
    blind_path = tmp_path / "blind.json"
    blind_path.write_text("{}", encoding="utf-8")
    paired = PairedPredictionManifest(
        run_id="QPRED-test",
        profile=quality_full_profile(
            provider="codex-oauth",
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        ),
        blind_selection={
            "artifact_path": "blind.json",
            "sha256": file_sha256(blind_path),
        },
        expected_case_ids=["CASE-1"],
        expected_variant_architecture_sha256={"V0": "0" * 64, "V1": "1" * 64},
        seals=[],
        paired_case_ids=[],
        all_predictions_sealed=False,
    )
    paired_path = tmp_path / "paired.json"
    write_json(paired_path, paired.model_dump(mode="json"))

    with pytest.raises(ValueError, match="every expected prediction seal"):
        score_runtime_variants(
            tmp_path,
            paired_prediction_manifest_path=paired_path,
            outcome_selection_path=tmp_path / "must-not-be-opened.json",
        )


def test_scoring_verifies_prediction_closure_before_opening_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blind_payload = _blind_selection_payload(tmp_path)
    provisional = BlindRuntimeSelection.model_validate(blind_payload)
    selection_id = quality_runtime_module.stable_id(
        "QSEL",
        canonical_json(
            {
                "version": quality_runtime_module.QUALITY_RUNTIME_SELECTION_VERSION,
                "source_selection_sha256": provisional.source_selection_sha256,
                "split": "CALIBRATION",
                "scope": "FULL_SPLIT",
                "cases": [case.model_dump(mode="json") for case in provisional.cases],
            }
        ),
        length=20,
    )
    blind_path = (
        tmp_path
        / quality_runtime_module.BLIND_SELECTION_ROOT
        / selection_id
        / quality_runtime_module.BLIND_SELECTION_FILENAME
    )
    blind_payload["selection_id"] = selection_id
    write_json(blind_path, blind_payload)
    blind_case = BlindRuntimeSelection.model_validate(blind_payload).cases[0]
    cutoff = blind_case.cutoff_at
    common = {
        "case_id": blind_case.episode_id,
        "sealed_at": cutoff,
        "cutoff_at": cutoff,
        "blind_input_manifest": blind_case.blind_input_manifest,
        "news_sha256": blind_case.news_sha256,
        "parsed_news_root_sha256": "0" * 64,
        "shared_context_sha256": "e" * 64,
        "brain_manifest": {
            "artifact_path": "brain/snapshots/test/brain_manifest.json",
            "sha256": "f" * 64,
        },
        "coverage_manifest": {
            "artifact_path": "brain/snapshots/test/coverage_manifest.json",
            "sha256": "1" * 64,
        },
        "memory_snapshot_id": "MEMIDX-test",
        "d_minus_one_context": {
            "artifact_path": "runs/shared/d-minus-one.json",
            "sha256": "2" * 64,
        },
        "d_minus_one_context_sha256": "2" * 64,
        "d_minus_one_candidate_universe_root_sha256": "6" * 64,
        "d_minus_one_snapshot_root_sha256": "7" * 64,
        "d_minus_one_source_revision_sha256": "0" * 64,
        "d_minus_one_snapshot_session_date": date(2030, 1, 9),
        "d_minus_one_payload_sha256": "8" * 64,
        "d_minus_one_consumed_payload_sha256": "8" * 64,
        "d_minus_one_projection_policy": ("ALL_PRELIMINARY_CANDIDATE_TICKERS_EXACT_SEALED_SUBSET.v1"),
        "d_minus_one_projection_root_sha256": "9" * 64,
        "d_minus_one_projection_requested_ticker_count": 0,
        "d_minus_one_projection_snapshot_count": 0,
        "d_minus_one_projection_missing_ticker_count": 0,
        "candidate_universe_policy_sha256": "3" * 64,
        "final_citation_count": 0,
    }
    seals = [
        PredictionSeal(
            **common,
            variant_id=variant_id,
            variant_architecture_sha256=("0" if variant_id == "V0" else "1") * 64,
            prediction={
                "artifact_path": f"predictions/{variant_id}.json",
                "sha256": "4" * 64,
            },
            context_manifest={
                "artifact_path": f"runs/manifests/{variant_id}.json",
                "sha256": "5" * 64,
            },
        )
        for variant_id in ("V0", "V1")
    ]
    paired = PairedPredictionManifest(
        run_id="QPRED-preflight",
        profile=quality_full_profile(
            provider="codex-oauth",
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        ),
        blind_selection={
            "artifact_path": blind_path.relative_to(tmp_path).as_posix(),
            "sha256": file_sha256(blind_path),
        },
        expected_case_ids=[blind_case.episode_id],
        expected_variant_architecture_sha256={"V0": "0" * 64, "V1": "1" * 64},
        shared_preparation_ledgers={
            blind_case.episode_id: {
                "artifact_path": "runs/shared/missing-ledger.json",
                "sha256": "a" * 64,
            }
        },
        seals=seals,
        paired_case_ids=[blind_case.episode_id],
        all_predictions_sealed=True,
    )
    paired_path = tmp_path / "paired.json"
    write_json(paired_path, paired.model_dump(mode="json"))

    def forbidden_outcome_open(_path: Path) -> object:
        raise AssertionError("outcome manifest opened before prediction closure")

    monkeypatch.setattr(
        quality_runtime_module,
        "load_runtime_outcome_selection",
        forbidden_outcome_open,
    )

    with pytest.raises(ValueError, match="source artifact (?:hash mismatch|is missing)"):
        score_runtime_variants(
            tmp_path,
            paired_prediction_manifest_path=paired_path,
            outcome_selection_path=tmp_path / "must-not-be-opened.json",
        )


@pytest.mark.parametrize("text_hash", [False, True])
def test_verified_json_reference_hashes_and_parses_one_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text_hash: bool,
) -> None:
    path = tmp_path / "sealed.json"
    write_json(path, {"status": "sealed"})
    reference = quality_runtime_module.QualityArtifactReference(
        artifact_path="sealed.json",
        sha256=(
            sha256_text(path.read_text(encoding="utf-8"))
            if text_hash
            else file_sha256(path)
        ),
    )
    sealed_bytes = path.read_bytes()
    original_read_bytes = Path.read_bytes
    read_count = 0

    def swapping_read_bytes(candidate: Path) -> bytes:
        nonlocal read_count
        if candidate.resolve() == path.resolve():
            read_count += 1
            if read_count > 1:
                return b'{"D_day_outcome":true}\n'
            return sealed_bytes
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_read_bytes)
    payload = quality_runtime_module._read_verified_json_reference(
        reference,
        path,
        text_hash=text_hash,
    )

    assert payload == {"status": "sealed"}
    assert read_count == 1


def _blind_selection_payload(root: Path) -> dict[str, object]:
    return {
        "schema_version": "nslab.blind_runtime_selection.v3",
        "selection_id": "QSEL-test",
        "source_selection_sha256": "a" * 64,
        "selection_policy": "ALL_SOURCE_SPLIT_CASES",
        "cases": [
            {
                "episode_id": "CASE-1",
                "trade_date": "2026-01-01",
                "split": "CALIBRATION",
                "cutoff_at": "2026-01-01T08:59:59+09:00",
                "blind_input_manifest": {
                    "artifact_path": (
                        "runs/semantic_brain_upgrade/quality_full/"
                        "blind_inputs/QINPUT-test/"
                        "sealed_blind_case_input_manifest.json"
                    ),
                    "sha256": "b" * 64,
                },
                "news_sha256": "c" * 64,
                "cutoff_safe_news_row_count": 1,
                "d_minus_one_context_sha256": "d" * 64,
                "d_minus_one_payload_sha256": "e" * 64,
                "d_minus_one_candidate_universe_root_sha256": "f" * 64,
                "d_minus_one_snapshot_root_sha256": "1" * 64,
                "d_minus_one_source_revision_sha256": "2" * 64,
                "d_minus_one_snapshot_session_date": "2025-12-31",
            }
        ],
        "outcome_reference_count": 0,
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
    }


def _source_case(root: Path, *, index: int, row_count: int) -> dict[str, object]:
    episode_id = f"CASE-{index}"
    episode_dir = root / "research" / episode_id
    episode_dir.mkdir(parents=True, exist_ok=True)
    normalized_path = episode_dir / "normalized.json"
    trade_date = date(2026, 1, index)
    write_json(
        normalized_path,
        {
            "trade_date": trade_date.isoformat(),
            "cutoff_at": f"{trade_date.isoformat()}T08:59:59+09:00",
        },
    )
    source_path = episode_dir / "source.jsonl"
    source_path.write_text(
        "".join(
            (
                '{"available_before_cutoff":true,'
                f'"source_id":"SRC-{row_index:06d}",'
                f'"published_at_kst":"{trade_date.isoformat()}T08:00:00+09:00",'
                '"title":"row","body":"body"}\n'
            )
            for row_index in range(1, row_count + 1)
        ),
        encoding="utf-8",
    )
    outcome_path = episode_dir / "outcome.jsonl"
    outcome_path.write_text("", encoding="utf-8")
    return {
        "episode_id": episode_id,
        "trade_date": trade_date.isoformat(),
        "split": "CALIBRATION",
        "normalized_index": {
            "artifact_path": normalized_path.relative_to(root).as_posix(),
            "sha256": file_sha256(normalized_path),
        },
        "source_ledger": {
            "artifact_path": source_path.relative_to(root).as_posix(),
            "sha256": file_sha256(source_path),
        },
        "outcome_ledger": {
            "artifact_path": outcome_path.relative_to(root).as_posix(),
            "sha256": file_sha256(outcome_path),
        },
    }


def _reseal_shared_package(
    result: shared_pre_retrieval_module.SharedPreRetrievalBuildResult,
    *,
    context_payload: dict[str, Any],
    input_cluster_root_sha256: str,
) -> str:
    candidate = SharedPreRetrievalContext.model_validate(context_payload)
    component_root = shared_pre_retrieval_module._component_artifact_root_sha256(
        references=(shared_pre_retrieval_module._named_context_references(candidate)),
        map_reduce_nodes=candidate.map_reduce_nodes,
    )
    context_payload["component_artifact_root_sha256"] = component_root
    write_json(result.context_path, context_payload)
    context_sha256 = file_sha256(result.context_path)
    manifest_payload = json.loads(result.manifest_path.read_bytes())
    manifest_payload["input_cluster_root_sha256"] = input_cluster_root_sha256
    manifest_payload["component_artifact_root_sha256"] = component_root
    manifest_payload["downstream_digest_payload_sha256"] = context_payload["downstream_digest_payload_sha256"]
    manifest_payload["context"]["sha256"] = context_sha256
    manifest_payload["identity_sha256"] = shared_pre_retrieval_module._content_identity_sha256(
        lookup_identity_sha256=manifest_payload["lookup_identity_sha256"],
        parsed_news_root_sha256=context_payload["parsed_news_root_sha256"],
        input_cluster_root_sha256=input_cluster_root_sha256,
        prompt_sha256_root=context_payload["prompt_sha256_root"],
        component_artifact_root_sha256=component_root,
        downstream_digest_payload_sha256=context_payload["downstream_digest_payload_sha256"],
        context_payload_sha256=context_sha256,
    )
    write_json(result.manifest_path, manifest_payload)
    return context_sha256


class _TestPriceSource:
    source_name = "sealed-d1-test"

    def __init__(
        self,
        *,
        close: float = 100.0,
        revision: str = "d" * 64,
    ) -> None:
        self.close = close
        self.source_revision_sha256 = revision
        self.universe_calls: list[date] = []

    def get_blind_snapshot_universe(self, *, through: date) -> list[PriceRecord]:
        self.universe_calls.append(through)
        return [
            PriceRecord(
                ticker="000001",
                trade_date=through,
                open=self.close,
                high=self.close,
                low=self.close,
                close=self.close,
                volume=1.0,
                amount=self.close,
                market_cap=self.close * 10.0,
                listed_shares=10.0,
            )
        ]


def _sealed_d_minus_one_kwargs(
    root: Path,
    *,
    trade_date: date,
    cutoff_at: datetime,
    close: float = 100.0,
) -> dict[str, object]:
    context = quality_runtime_module._build_privileged_d_minus_one_context(
        _TestPriceSource(close=close),
        trade_date=trade_date,
        cutoff_at=cutoff_at,
    )
    payload = canonical_json(context.model_dump(mode="json")) + "\n"
    input_id = "QINPUT-" + sha256_text(payload)[:20]
    artifact_path = root / quality_runtime_module.BLIND_INPUT_ROOT / input_id / "d_minus_one_safe_context.json"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    if artifact_path.exists():
        assert artifact_path.read_text(encoding="utf-8") == payload
    else:
        artifact_path.write_text(payload, encoding="utf-8")
    reference = QualityArtifactReference(
        artifact_path=artifact_path.relative_to(root).as_posix(),
        sha256=file_sha256(artifact_path),
    )
    return {
        "d_minus_one_context": context,
        "d_minus_one_reference": reference,
    }


def _shared_builder_project(tmp_path: Path) -> tuple[Settings, Path]:
    settings = Settings(project_root=tmp_path)
    settings.limits.open_world_cluster_batch_size = 1
    settings.limits.novelty_cluster_batch_size = 1
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path, settings=settings).rebuild(mode="catalog")
    news_path = tmp_path / "shared.csv"
    news_path.write_text(
        "date,time,title,body\n"
        '2030-01-10,08:00:00,"가상기업 공급계약 확정","100억원 계약을 체결했다. 추가 조건은 검토 중이다."\n'
        '2030-01-10,08:10:00,"다른기업 대표 사임","대표가 사임했다. 후임은 미정이다."\n'
        '2030-01-10,08:20:00,"세번째기업 투자 승인","정부가 50억원 투자를 승인했다. 집행일은 미정이다."\n',
        encoding="utf-8-sig",
    )
    return settings, news_path
