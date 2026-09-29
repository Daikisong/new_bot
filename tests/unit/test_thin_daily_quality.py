from __future__ import annotations

import inspect
from datetime import date, datetime

import pytest
from pydantic import ValidationError

from news_scalping_lab.contracts.offline_brain import (
    CurrentEventCapsule,
    DailyBrainContext,
    SynthesizedMechanismClaim,
)
from news_scalping_lab.contracts.quality_evaluation import (
    BlindRuntimeCase,
    QualityArtifactReference,
    ThinDailyQualityPredictionManifest,
    quality_full_runtime_profile,
)
from news_scalping_lab.evaluation.thin_daily_quality import (
    NoHistoricalBrainContextProvider,
    _render_score_markdown,
    _validate_build_only_v2_package,
    _validate_quality_cases_against_build_split,
    _verify_build_only_attestation_payload,
    predict_thin_daily_quality,
)
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    sha256_text,
    write_json,
)


def _capsule() -> CurrentEventCapsule:
    return CurrentEventCapsule(
        cluster_id="CLUSTER-1",
        source_row_ids=[1],
        event_ids=["EVENT-1"],
        source_ids=["SOURCE-1"],
        representative_title="Issuer signed a contract",
        predicate_exact_sentences=["Issuer signed a contract"],
        published_times=[datetime(2026, 1, 2, 7, 30, tzinfo=KST)],
    )


def _runtime_case(episode_id: str, split: str, trade_date: date) -> BlindRuntimeCase:
    digest = "6" * 64
    return BlindRuntimeCase(
        episode_id=episode_id,
        trade_date=trade_date,
        split=split,  # type: ignore[arg-type]
        cutoff_at=datetime.combine(trade_date, datetime.min.time(), tzinfo=KST),
        blind_input_manifest=QualityArtifactReference(
            artifact_path="runs/blind_input.json",
            sha256=digest,
        ),
        news_sha256=digest,
        cutoff_safe_news_row_count=1,
        d_minus_one_context_sha256=digest,
        d_minus_one_payload_sha256=digest,
        d_minus_one_candidate_universe_root_sha256=digest,
        d_minus_one_snapshot_root_sha256=digest,
        d_minus_one_source_revision_sha256=digest,
    )


@pytest.mark.asyncio
async def test_arm_a_provider_returns_only_current_news_binding() -> None:
    current_capsule = _capsule()
    context = await NoHistoricalBrainContextProvider().retrieve(
        interpretation=None,
        current_event_capsules=[current_capsule],
        cutoff_at=datetime(2026, 1, 2, 8, 0, tzinfo=KST),
        max_exact_witnesses=24,
    )

    assert context.brain_projection_mode == "NO_HISTORICAL_BRAIN"
    assert context.brain_version == "NO_HISTORICAL_BRAIN"
    assert context.retrieval_basis == "CURRENT_NEWS"
    assert context.current_event_capsules_sha256 == sha256_text(
        canonical_json([current_capsule.model_dump(mode="json")])
    )
    assert context.selected_semantic_capsules == []
    assert context.selected_mechanism_claims == []
    assert context.compiled_brain_guidance == []
    assert context.exact_witnesses == []


def test_no_brain_context_contract_rejects_historical_claims() -> None:
    claim = SynthesizedMechanismClaim(
        claim_id="CLAIM-1",
        category="single_event",
        statement="A supported mechanism.",
        mechanism="event -> exposure -> response",
        available_from=datetime(2025, 1, 1, tzinfo=KST),
        confidence="medium",
        status="supported",
        source_node_ids=["NODE-1"],
    )
    with pytest.raises(ValidationError, match="historical knowledge"):
        DailyBrainContext(
            brain_version="NO_HISTORICAL_BRAIN",
            brain_package_root="1" * 64,
            brain_build_cutoff=datetime(2026, 1, 2, tzinfo=KST),
            brain_projection_mode="NO_HISTORICAL_BRAIN",
            retrieval_basis="CURRENT_NEWS",
            current_event_capsules_sha256="2" * 64,
            selected_mechanism_claims=[claim],
        )


def test_prediction_manifest_cannot_claim_completion_without_paired_seals() -> None:
    profile = quality_full_runtime_profile(
        provider="codex-oauth",
        model="gpt-5.6-sol",
        reasoning_effort="xhigh",
    )
    architectures = {arm: str(index) * 64 for index, arm in enumerate("ABC", start=1)}
    base = {
        "run_id": "THINQUAL-test",
        "profile": profile,
        "blind_selection": QualityArtifactReference(
            artifact_path="runs/blind_runtime_selection.json",
            sha256="4" * 64,
        ),
        "build_only_source_attestation": QualityArtifactReference(
            artifact_path="runs/build_only_source_attestation.json",
            sha256="5" * 64,
        ),
        "expected_case_ids": ["CASE-1"],
        "expected_arm_ids": ["A", "B", "C"],
        "expected_arm_architecture_sha256": architectures,
    }

    incomplete = ThinDailyQualityPredictionManifest(**base)
    assert not incomplete.all_predictions_sealed
    with pytest.raises(ValidationError, match="completion flag is stale"):
        ThinDailyQualityPredictionManifest(
            **base,
            all_predictions_sealed=True,
        )


def test_blind_prediction_entrypoint_has_no_outcome_parameter() -> None:
    parameters = inspect.signature(predict_thin_daily_quality).parameters
    assert "blind_selection_path" in parameters
    assert "outcome_selection_path" not in parameters
    assert "truth" not in parameters


def test_formal_package_requires_evaluation_only_build_snapshot(tmp_path) -> None:
    source_root = tmp_path / "source"
    package_root = tmp_path / "package"
    (source_root / "memory" / "retrieval_index").mkdir(parents=True)
    package_root.mkdir()
    write_json(
        package_root / "offline_compile_manifest.json",
        {
            "source_project": str(source_root),
            "source_pointer_manifest_hash_match": True,
        },
    )
    write_json(package_root / "brain_package_manifest.json", {})
    write_json(
        source_root / "memory" / "retrieval_index" / "current.json",
        {"evaluation_only": False},
    )

    with pytest.raises(ValueError, match="evaluation-only BUILD snapshot"):
        _validate_build_only_v2_package(package_root)


def test_build_only_package_attestation_verifies_snapshot_and_split_chain(tmp_path) -> None:
    source_root = tmp_path / "source"
    package_root = tmp_path / "package"
    package_root.mkdir()
    records_dir = source_root / "memory" / "records"
    records_dir.mkdir(parents=True)
    for episode_id, record_id in (("CAL-1", "CAL-REC"), ("HOLD-1", "HOLD-REC")):
        (records_dir / f"{episode_id}.jsonl").write_text(
            canonical_json({"record_id": record_id}) + "\n",
            encoding="utf-8",
        )

    ledger_path = source_root / "memory" / "record_hashes.jsonl"
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text(canonical_json({"record_id": "BUILD-REC"}) + "\n", encoding="utf-8")
    ledger_sha256 = file_sha256(ledger_path)
    database_path = source_root / "memory" / "snapshot.duckdb"
    database_path.write_bytes(b"test database commitment")
    database_sha256 = file_sha256(database_path)

    split_root = source_root / "runs" / "semantic_brain_upgrade" / "shadow_split"
    split_root.mkdir(parents=True)
    plan_path = split_root / "shadow_split_plan.json"
    write_json(plan_path, {"plan": "test"})
    split_selection_path = split_root / "shadow_case_selection.json"
    split_selection = {
        "schema_version": "nslab.semantic_upgrade_split_selection.v1",
        "plan_sha256": file_sha256(plan_path),
        "build_case_count": 1,
        "calibration_case_count": 1,
        "holdout_case_count": 1,
        "cases": [
            {"episode_id": "BUILD-1", "split": "BUILD"},
            {"episode_id": "CAL-1", "split": "CALIBRATION"},
            {"episode_id": "HOLD-1", "split": "HOLDOUT"},
        ],
    }
    write_json(split_selection_path, split_selection)
    split_selection_sha256 = file_sha256(split_selection_path)

    snapshot_id = "MEMIDX-test-build"
    build_cutoff = "2026-01-02T00:00:00+09:00"
    snapshot_manifest_path = source_root / "memory" / "retrieval_index" / "snapshots" / snapshot_id / "manifest.json"
    snapshot_manifest_path.parent.mkdir(parents=True)
    snapshot_manifest = {
        "snapshot_id": snapshot_id,
        "evaluation_only": True,
        "availability_mode": "replay_available_from",
        "as_of_cutoff": build_cutoff,
        "record_count": 1,
        "real_embedding": True,
        "embedding_model": "minilm-test",
        "database": {
            "artifact_path": "memory/snapshot.duckdb",
            "sha256": database_sha256,
        },
        "source_record_hashes": {
            "artifact_path": "memory/record_hashes.jsonl",
            "sha256": ledger_sha256,
        },
    }
    write_json(snapshot_manifest_path, snapshot_manifest)
    snapshot_manifest_sha256 = file_sha256(snapshot_manifest_path)

    replay_receipt_path = (
        source_root
        / "runs"
        / "semantic_brain_upgrade"
        / "replay_snapshots"
        / snapshot_id
        / "shadow_replay_snapshot_receipt.json"
    )
    replay_receipt_path.parent.mkdir(parents=True)
    calibration_id_sha = sha256_text("CAL-REC")
    holdout_id_sha = sha256_text("HOLD-REC")
    write_json(
        replay_receipt_path,
        {
            "schema_version": "nslab.shadow_replay_as_of_snapshot.v1",
            "availability_mode": "replay_available_from",
            "snapshot_id": snapshot_id,
            "build_cutoff": build_cutoff,
            "record_count": 1,
            "source_snapshot_record_count": 3,
            "source_record_hashes_sha256": ledger_sha256,
            "calibration_record_count": 1,
            "calibration_record_ids_sha256": calibration_id_sha,
            "calibration_overlap_count": 0,
            "holdout_record_count": 1,
            "holdout_record_ids_sha256": holdout_id_sha,
            "holdout_overlap_count": 0,
            "full_corpus_centroids_used": False,
            "generated_embedding_count": 0,
        },
    )
    evaluation_brain_version = "brain-build-test"
    evaluation_brain_manifest_path = source_root / "brain" / "current" / "brain_manifest.json"
    evaluation_brain_manifest_path.parent.mkdir(parents=True)
    write_json(evaluation_brain_manifest_path, {"brain_version": evaluation_brain_version})
    evaluation_brain_receipt_path = (
        source_root
        / "runs"
        / "semantic_brain_upgrade"
        / "evaluation_brains"
        / evaluation_brain_version
        / "evaluation_brain_receipt.json"
    )
    evaluation_brain_receipt_path.parent.mkdir(parents=True)
    write_json(
        evaluation_brain_receipt_path,
        {
            "evaluation_only": True,
            "memory_snapshot_id": snapshot_id,
            "split_selection_sha256": split_selection_sha256,
        },
    )

    pointer_path = source_root / "memory" / "retrieval_index" / "current.json"
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(
        pointer_path,
        {
            "evaluation_only": True,
            "snapshot_id": snapshot_id,
            "manifest_path": snapshot_manifest_path.relative_to(source_root).as_posix(),
            "manifest_sha256": snapshot_manifest_sha256,
            "evaluation_database_sha256": database_sha256,
            "evaluation_receipt_path": replay_receipt_path.relative_to(source_root).as_posix(),
            "evaluation_receipt_sha256": file_sha256(replay_receipt_path),
        },
    )
    write_json(
        source_root / "memory" / "record_index" / "manifest.json",
        {"record_count": 3, "full_envelope_root_sha256": "a" * 64},
    )

    write_json(
        package_root / "offline_compile_manifest.json",
        {
            "source_project": str(source_root),
            "source_pointer_manifest_hash_match": True,
            "source_memory_snapshot_id": snapshot_id,
            "source_memory_manifest_sha256": snapshot_manifest_sha256,
            "source_pointer_manifest_sha256": snapshot_manifest_sha256,
            "record_count": 1,
            "record_corpus_root": "a" * 64,
            "embedding_identity": "minilm-test",
            "compile_id": "OFFLINE-COMPILE-test",
        },
    )
    write_json(
        package_root / "brain_package_manifest.json",
        {
            "memory_snapshot_root": snapshot_manifest_sha256,
            "record_count": 1,
            "build_cutoff": build_cutoff,
            "record_corpus_root": "a" * 64,
            "production_eligible": False,
            "package_root": "b" * 64,
            "brain_version": "brain-v2-test",
        },
    )

    attestation = _validate_build_only_v2_package(package_root)
    _verify_build_only_attestation_payload(attestation.payload)
    assert attestation.payload["build_snapshot_record_count"] == 1
    assert attestation.payload["calibration_overlap_count"] == 0
    assert attestation.payload["holdout_overlap_count"] == 0


def test_formal_cases_must_match_the_attested_split() -> None:
    cutoff = datetime(2026, 1, 2, 0, 0, tzinfo=KST)
    split_ids = {
        "BUILD": ["BUILD-1"],
        "CALIBRATION": ["CAL-1"],
        "HOLDOUT": ["HOLD-1"],
    }
    cases = [
        _runtime_case("CAL-1", "CALIBRATION", date(2026, 1, 2)),
        _runtime_case("HOLD-1", "HOLDOUT", date(2026, 1, 3)),
        _runtime_case("POST-1", "POST_CUTOFF", date(2026, 1, 5)),
    ]

    _validate_quality_cases_against_build_split(
        cases,
        split_case_ids=split_ids,
        build_cutoff=cutoff,
    )
    with pytest.raises(ValueError, match="attested BUILD split"):
        _validate_quality_cases_against_build_split(
            [_runtime_case("BUILD-1", "CALIBRATION", date(2026, 1, 2))],
            split_case_ids=split_ids,
            build_cutoff=cutoff,
        )


def test_build_only_attestation_is_content_bound_and_closes_exclusions() -> None:
    core = {
        "schema_version": "nslab.thin_daily_build_source_attestation.v1",
        "calibration_overlap_count": 0,
        "holdout_overlap_count": 0,
        "full_corpus_centroids_used": False,
        "generated_embedding_count": 0,
    }
    payload = {
        **core,
        "attestation_sha256": sha256_text(canonical_json(core)),
    }
    _verify_build_only_attestation_payload(payload)

    with pytest.raises(ValueError, match="attestation hash is invalid"):
        _verify_build_only_attestation_payload(
            {**payload, "full_corpus_centroids_used": True}
        )


def test_score_markdown_surfaces_build_snapshot_and_exclusion_evidence() -> None:
    report = {
        "split": "CALIBRATION",
        "case_count": 3,
        "run_id": "THINQUAL-test",
        "build_only_source_summary": {
            "attestation_sha256": "a" * 64,
            "source_memory_snapshot_id": "MEMIDX-build",
            "build_cutoff": "2026-01-02T00:00:00+09:00",
            "source_record_count": 100,
            "build_snapshot_record_count": 75,
            "calibration_case_count": 1,
            "calibration_record_count": 10,
            "calibration_overlap_count": 0,
            "holdout_case_count": 1,
            "holdout_record_count": 15,
            "holdout_overlap_count": 0,
            "full_corpus_centroids_used": False,
            "generated_embedding_count": 0,
        },
        "build_only_source_attestation": {
            "artifact_path": "runs/build_only_source_attestation.json",
            "sha256": "b" * 64,
        },
        "quality_metrics_by_arm": {arm: {} for arm in ("A", "B", "C")},
        "unavailable_metrics": {},
        "promotion_decision": "HOLD_FOR_REGISTERED_QUALITY_GATES_AND_EXTERNAL_REVIEW",
    }

    rendered = _render_score_markdown(report)

    assert "MEMIDX-build" in rendered
    assert "75/100 source records included" in rendered
    assert "CAL 10 records / 0 overlap" in rendered
    assert "HOLDOUT 15 records / 0 overlap" in rendered
    assert "runs/build_only_source_attestation.json" in rendered
    assert "a" * 64 in rendered
