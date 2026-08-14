from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import cast
from unittest.mock import patch

import duckdb
import pytest
from typer.testing import CliRunner

import news_scalping_lab.cli as cli_module
import news_scalping_lab.evaluation.shadow as shadow_module
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    MemoryCellSnapshotManifest,
    NumericDistribution,
)
from news_scalping_lab.contracts.models import OutcomeLabels
from news_scalping_lab.contracts.shadow_evaluation import (
    SHADOW_ARM_FEATURES,
    ShadowArmAttestation,
    ShadowArmFeatures,
    ShadowArmObservation,
    ShadowAsOfSnapshot,
    ShadowCandidateObservation,
    ShadowDatasetKind,
    ShadowExecutionIdentity,
    ShadowLoadAttestation,
    ShadowLoadProfile,
    ShadowOutcomeTruth,
    ShadowReplayCase,
    ShadowReplayDataset,
    ShadowRetrievedRecord,
    ShadowSystemObservation,
    ShadowTruthAttestation,
)
from news_scalping_lab.evaluation.shadow import (
    ShadowReplayEvaluator,
    seal_shadow_arm_observation,
    seal_shadow_case_truth,
    seal_shadow_dataset,
    seal_shadow_load_profile,
    seal_shadow_split,
    shadow_arm_source_payload,
    shadow_replay_readiness,
    shadow_split_commitment_payload,
    verify_shadow_split_attestation,
)
from news_scalping_lab.llm.openai_provider import OpenAIResponsesProvider
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.prices.base import PriceSource
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)

_ZERO_SHA = "0" * 64
_SHADOW_KEY = "phase8-shadow-pre-registration-key-32-bytes-minimum"
_RUNNER_KEY = "phase8-shadow-runner-attestation-key-32-bytes-minimum"
_TRUTH_KEY = "phase8-shadow-truth-attestation-key-32-bytes-minimum"
_SEALED_AT = datetime(2029, 12, 31, 23, 59, 59, tzinfo=KST)
_DATASET_CREATED_AT = datetime(2030, 2, 21, 12, 0, 0, tzinfo=KST)
_TEST_MEMORY_INDEX = cast(ProductionMemoryIndex, object())
_REAL_PRODUCTION_RETRIEVED_RECORD_ERRORS = (
    shadow_module._production_retrieved_record_errors
)
_REAL_PRODUCTION_PROVIDER_ERRORS = ShadowReplayEvaluator._production_provider_errors


class _FixturePriceSource:
    source_name = "historical-fixture"

    def get_outcome_universe(self, *, trade_date: date) -> dict[str, OutcomeLabels]:
        del trade_date
        return {
            item.ticker: OutcomeLabels(
                intraday_high_return_pct=item.high_return_pct,
                close_return_pct=item.close_return_pct,
                upper_limit_touched=item.upper_limit_touched,
            )
            for item in _outcomes()
        }


_TEST_PRICE_SOURCE = cast(PriceSource, _FixturePriceSource())


@pytest.fixture(autouse=True)
def _deep_snapshot_inspection_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NSLAB_SHADOW_RUNNER_HMAC_KEY", _RUNNER_KEY)
    monkeypatch.setenv("NSLAB_SHADOW_TRUTH_HMAC_KEY", _TRUTH_KEY)
    monkeypatch.setattr(
        shadow_module,
        "inspect_memory_snapshot",
        lambda root, snapshot_id: {
            "passed": True,
            "production_ready": True,
            "snapshot_id": snapshot_id,
        },
    )
    monkeypatch.setattr(
        shadow_module,
        "_production_retrieved_record_errors",
        lambda root, case, arm, manifest: [],
    )
    monkeypatch.setattr(
        shadow_module,
        "audit_lookahead",
        lambda root, memory_index=None: {
            "passed": True,
            "findings": [],
            "checked_manifests": 240,
        },
    )
    monkeypatch.setattr(
        ShadowReplayEvaluator,
        "_production_provider_errors",
        lambda self, dataset: [],
    )


def _ref(root: Path, relative: str, payload: object) -> ArtifactReference:
    path = root / relative
    write_json(path, payload)
    return ArtifactReference(
        artifact_path=relative,
        sha256=file_sha256(path),
        item_count=1,
    )


def _jsonl_ref(
    root: Path,
    relative: str,
    rows: list[dict[str, object]],
) -> ArtifactReference:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return ArtifactReference(
        artifact_path=relative,
        sha256=file_sha256(path),
        item_count=len(rows),
    )


def _placeholder_ref(relative: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative,
        sha256=_ZERO_SHA,
        item_count=1,
    )


def _news_csv_ref(
    root: Path,
    *,
    relative: str,
    trade_day: date,
) -> ArtifactReference:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "date,time,title,body\n"
        f"{trade_day.isoformat()},08:00:00,Historical catalyst,Cutoff safe body\n",
        encoding="utf-8",
    )
    return ArtifactReference(
        artifact_path=relative,
        sha256=file_sha256(path),
        item_count=1,
    )


def _distribution(value: float) -> NumericDistribution:
    return NumericDistribution(
        count=5,
        minimum=value,
        mean=value,
        p50=value,
        p95=value,
        p99=value,
        maximum=value,
    )


def _empty_distribution() -> NumericDistribution:
    return NumericDistribution(
        count=0,
        minimum=0.0,
        mean=0.0,
        p50=0.0,
        p95=0.0,
        p99=0.0,
        maximum=0.0,
    )


def _features(arm_id: str) -> ShadowArmFeatures:
    values = SHADOW_ARM_FEATURES[arm_id]
    return ShadowArmFeatures(
        legacy_top3=values[0],
        memory_cells=values[1],
        population_statistics=values[2],
        representatives=values[3],
        adaptive_drill_down=values[4],
    )


def _telemetry(arm_id: str) -> ShadowSystemObservation:
    memory_arm = arm_id in {"E", "F"}
    return ShadowSystemObservation(
        pre_llm_latency_ms=1_000.0 if memory_arm else 400.0,
        daily_analysis_latency_ms=20_000.0 if memory_arm else 10_000.0,
        llm_input_tokens=30_000 if memory_arm else 10_000,
        llm_output_tokens=2_000,
        embedding_query_count=2 if arm_id != "A" else 0,
        cache_hit_count=1 if arm_id != "A" else 0,
        cache_lookup_count=2 if arm_id != "A" else 0,
        peak_memory_bytes=256 * 1024 * 1024,
        estimated_cost_usd=0.25 if memory_arm else 0.1,
        online_full_scan_count=0,
    )


def _execution(
    cutoff: datetime,
    *,
    dataset_kind: ShadowDatasetKind,
) -> ShadowExecutionIdentity:
    historical = dataset_kind == "SEALED_HISTORICAL_REPLAY"
    return ShadowExecutionIdentity(
        execution_mode=dataset_kind,
        runner_protocol_version="shadow_fixture_runner.v1",
        llm_provider="openai" if historical else "fixture",
        llm_model="gpt-shadow-test" if historical else "fixture-model",
        prompt_version="shadow_ablation_prompt.v1",
        inference_config_sha256="a" * 64,
        started_at=cutoff - timedelta(minutes=10),
        completed_at=cutoff - timedelta(minutes=1),
        production_provider_attested=historical,
    )


def _brain_ref(
    root: Path,
    *,
    case_id: str,
    cutoff: datetime,
    production_snapshot_id: str,
) -> tuple[str, ArtifactReference]:
    brain_version = f"BRAIN-{case_id}"
    relative = f"brain/snapshots/{brain_version}/brain_manifest.json"
    payload = {
        "schema_version": "nslab.brain_manifest.v1",
        "brain_version": brain_version,
        "created_at": cutoff.isoformat(),
        "build_mode": "llm-full",
        "catalog_only": False,
        "production_eligible": True,
        "accepted_episode_count": 0,
        "covered_episode_count": 0,
        "covered_episode_ids": [],
        "source_hashes": {},
        "brain_record_cutoff_at": cutoff.isoformat(),
        "production_memory_snapshot_id": production_snapshot_id,
        "production_memory_corpus_sha256": "1" * 64,
        "production_memory_source_generation_sha256": "2" * 64,
        "production_memory_as_of_cutoff": cutoff.isoformat(),
        "coverage_complete": True,
    }
    return brain_version, _ref(root, relative, payload)


def _snapshot(
    root: Path,
    case_id: str,
    cutoff: datetime,
    *,
    record_count: int = 1,
) -> ShadowAsOfSnapshot:
    snapshot_id = f"SNAP-{case_id}"
    relative = (
        f"memory/retrieval_index/snapshots/{snapshot_id}/manifest.json"
    )
    one = {
        "schema_version": "nslab.memory_artifact_reference.v1",
        "artifact_path": f"fixtures/shadow/{case_id}/internal.jsonl",
        "sha256": "3" * 64,
        "item_count": 1,
    }
    zero = {**one, "item_count": 0}
    record_artifact = {**one, "item_count": record_count}
    payload = {
        "schema_version": "nslab.memory_cell_snapshot_manifest.v3",
        "snapshot_id": snapshot_id,
        "as_of_cutoff": cutoff.isoformat(),
        "cutoff_identity": f"explicit:{cutoff.isoformat()}",
        "max_available_from": (cutoff - timedelta(days=1)).isoformat(),
        "corpus_manifest_sha256": "1" * 64,
        "source_generation_sha256": "2" * 64,
        "embedding_provider": "openai",
        "embedding_model": "llm_embedding:test:1536",
        "real_embedding": True,
        "embedding_dimensions": 1536,
        "clustering_version": "memory_cells.v1",
        "normalizer_version": "cutoff_safe.v1",
        "cell_schema_version": "memory_cell.v1",
        "polarity_classifier_version": "polarity.v1",
        "population_projection_version": "population_projection.v1",
        "routing_metadata_sha256": "4" * 64,
        "record_count": record_count,
        "excluded_future_record_count": 0,
        "next_available_from": None,
        "reasoning_record_count": record_count,
        "context_record_count": 0,
        "audit_record_count": 0,
        "quarantined_record_count": 0,
        "cell_count": 1,
        "primary_membership_count": record_count,
        "secondary_membership_count": 0,
        "independent_unit_count": record_count,
        "unsupported_reasoning_record_count": 0,
        "unsupported_reasoning_record_ids_sha256": "5" * 64,
        "parent_snapshot_id": None,
        "retained_record_count": 0,
        "added_record_count": record_count,
        "source_record_hashes": record_artifact,
        "excluded_future_record_hashes": zero,
        "routing_metadata": record_artifact,
        "embedding_hashes": record_artifact,
        "cell_entries": one,
        "memberships": record_artifact,
        "database": record_artifact,
        "metadata_index_ready": True,
        "fts_index_ready": True,
        "hnsw_index_ready": True,
        "provenance_graph_ready": True,
        "production_ready": True,
    }
    brain_version, brain_ref = _brain_ref(
        root,
        case_id=case_id,
        cutoff=cutoff,
        production_snapshot_id=str(payload["snapshot_id"]),
    )
    return ShadowAsOfSnapshot(
        snapshot_kind="PRODUCTION_MEMORY_CELLS",
        snapshot_id=payload["snapshot_id"],
        as_of_cutoff=cutoff,
        corpus_manifest_sha256=payload["corpus_manifest_sha256"],
        source_generation_sha256=payload["source_generation_sha256"],
        embedding_model=payload["embedding_model"],
        clustering_version=payload["clustering_version"],
        normalizer_version=payload["normalizer_version"],
        snapshot_manifest=_ref(root, relative, payload),
        brain_version=brain_version,
        brain_manifest=brain_ref,
    )


def _legacy_snapshot(
    root: Path,
    case_id: str,
    cutoff: datetime,
    production_snapshot: ShadowAsOfSnapshot,
) -> ShadowAsOfSnapshot:
    relative = f"fixtures/shadow/{case_id}/legacy_snapshot.json"
    index_ref = _jsonl_ref(
        root,
        f"fixtures/shadow/{case_id}/legacy_top3.jsonl",
        [{"record_id": "R1"}],
    )
    payload = {
        "schema_version": "nslab.shadow_legacy_top3_snapshot.v1",
        "snapshot_id": f"LEGACY-{case_id}",
        "as_of_cutoff": cutoff.isoformat(),
        "corpus_manifest_sha256": "1" * 64,
        "source_generation_sha256": "2" * 64,
        "embedding_model": "legacy_embedding:test",
        "clustering_version": "legacy_top3.v1",
        "normalizer_version": "legacy_document.v1",
        "record_count": 1,
        "top_record_ids": ["R1"],
        "index_artifact": index_ref.model_dump(mode="json"),
    }
    return ShadowAsOfSnapshot(
        snapshot_kind="LEGACY_TOP3_INDEX",
        snapshot_id=payload["snapshot_id"],
        as_of_cutoff=cutoff,
        corpus_manifest_sha256=payload["corpus_manifest_sha256"],
        source_generation_sha256=payload["source_generation_sha256"],
        embedding_model=payload["embedding_model"],
        clustering_version=payload["clustering_version"],
        normalizer_version=payload["normalizer_version"],
        snapshot_manifest=_ref(root, relative, payload),
        brain_version=production_snapshot.brain_version,
        brain_manifest=production_snapshot.brain_manifest,
    )


def _outcomes() -> list[ShadowOutcomeTruth]:
    return [
        ShadowOutcomeTruth(
            ticker="T1",
            high_return_pct=25.0,
            close_return_pct=18.0,
            upper_limit_touched=False,
            candidate_relevant=True,
            actual_theme_id="THEME-1",
            is_theme_leader=True,
            newsless=False,
        ),
        ShadowOutcomeTruth(
            ticker="T2",
            high_return_pct=-5.0,
            close_return_pct=-7.0,
            upper_limit_touched=False,
            candidate_relevant=False,
            actual_theme_id=None,
            is_theme_leader=False,
            newsless=True,
        ),
        ShadowOutcomeTruth(
            ticker="T3",
            high_return_pct=12.0,
            close_return_pct=8.0,
            upper_limit_touched=False,
            candidate_relevant=True,
            actual_theme_id="THEME-1",
            is_theme_leader=False,
            newsless=False,
        ),
    ]


def _retrieved_records(trade_day: date, cutoff: datetime) -> list[ShadowRetrievedRecord]:
    available_from = cutoff - timedelta(days=1)
    source_day = trade_day - timedelta(days=365)
    return [
        ShadowRetrievedRecord(
            record_id="R1",
            independent_unit_id=(
                f"EVENT_ISSUER_DAY:{source_day}:EVENT-R1:T1"
            ),
            record_type="supervised_direct_event_case",
            memory_lanes=["positive_analogs"],
            evidence_polarity="POSITIVE",
            ticker="T1",
            trade_date=source_day,
            available_from=available_from,
            regime_cluster="RISK_ON",
        ),
        ShadowRetrievedRecord(
            record_id="NEG1",
            independent_unit_id=f"ISSUER_DAY:{source_day}:T2",
            record_type="negative_control_case",
            memory_lanes=["negative_controls"],
            evidence_polarity="NEGATIVE",
            ticker="T2",
            trade_date=source_day,
            available_from=available_from,
            regime_cluster="RISK_OFF",
        ),
        ShadowRetrievedRecord(
            record_id="COUNTER1",
            independent_unit_id=f"ISSUER_DAY:{source_day}:T2",
            record_type="counterexample",
            memory_lanes=["counterexamples"],
            evidence_polarity="NEGATIVE",
            ticker="T2",
            trade_date=source_day,
            available_from=available_from,
            regime_cluster="RISK_OFF",
        ),
        ShadowRetrievedRecord(
            record_id="LONG1",
            independent_unit_id=(
                f"THEME_DAY_TICKER_DAY:{source_day}:THEME-1:T3"
            ),
            record_type="beneficiary_discovery_case",
            memory_lanes=["positive_analogs"],
            evidence_polarity="POSITIVE",
            ticker="T3",
            trade_date=source_day,
            available_from=available_from,
            regime_cluster="RISK_ON",
        ),
    ]


def _candidates(
    arm_id: str,
    *,
    split: str,
    calibration_index: int,
) -> list[ShadowCandidateObservation]:
    if arm_id == "B":
        ticker = "T1" if split == "CALIBRATION" and calibration_index % 2 == 0 else "T2"
        return [
            ShadowCandidateObservation(
                rank=1,
                ticker=ticker,
                company_name=f"Company {ticker}",
                confidence_label="high",
                claimed_theme_id="THEME-1",
                claims_news_cause=ticker == "T2",
                memory_record_ids=["R1"],
            )
        ]
    if arm_id in {"E", "F"}:
        return [
            ShadowCandidateObservation(
                rank=1,
                ticker="T1",
                company_name="Company T1",
                confidence_label="high",
                claimed_theme_id="THEME-1",
                claims_news_cause=False,
                memory_record_ids=["R1"],
            ),
            ShadowCandidateObservation(
                rank=2,
                ticker="T3",
                company_name="Company T3",
                confidence_label="high",
                claimed_theme_id="THEME-1",
                claims_news_cause=False,
                memory_record_ids=["LONG1"],
            ),
        ]
    ticker = "T1" if arm_id in {"C", "D"} else "T2"
    return [
        ShadowCandidateObservation(
            rank=1,
            ticker=ticker,
            company_name=f"Company {ticker}",
            confidence_label="high",
            claimed_theme_id="THEME-1" if ticker == "T1" else None,
            claims_news_cause=False,
            memory_record_ids=[] if arm_id == "A" else ["R1"],
        )
    ]


def _historical_arm_source_refs(
    root: Path,
    *,
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
    observation_ref: ArtifactReference,
) -> list[ArtifactReference]:
    run_id = f"RUN-{case.case_id}-{arm.arm_id}"
    prediction_relative = (
        f"runs/checkpoints/output_artifacts/{run_id}/blind_prediction.json"
    )
    prediction_payload = {
        "schema_version": "nslab.blind_prediction.v1",
        "prediction_id": arm.prediction_id,
        "trade_date": case.trade_date.isoformat(),
        "cutoff_at": case.replay_cutoff_at.isoformat(),
        "created_at": arm.execution.completed_at.isoformat(),
        "sealed_at": arm.execution.completed_at.isoformat(),
        "blind_analysis": {
            "summary": "Sealed historical replay fixture",
        },
        "candidates": [
            {
                "rank": candidate.rank,
                "ticker": candidate.ticker,
                "company_name": candidate.company_name,
                "path_type": "SINGLE_EVENT",
                "claimed_theme_id": candidate.claimed_theme_id,
                "claims_news_cause": candidate.claims_news_cause,
                "thesis": "Cutoff-safe replay candidate",
                "why_now": "Historical fixture event",
                "confidence_label": candidate.confidence_label,
                "memory_record_ids": candidate.memory_record_ids,
            }
            for candidate in arm.candidates
        ],
        "context_manifest_id": run_id,
    }
    prediction_ref = _ref(root, prediction_relative, prediction_payload)
    context_relative = f"runs/manifests/{run_id}.json"
    context_payload = {
        "schema_version": "nslab.context_manifest.v1",
        "run_id": run_id,
        "mode": "exhaustive",
        "trade_date": case.trade_date.isoformat(),
        "cutoff_at": case.replay_cutoff_at.isoformat(),
        "as_of": case.replay_cutoff_at.isoformat(),
        "created_at": arm.execution.completed_at.isoformat(),
        "news_file": case.news_artifact.artifact_path,
        "news_sha256": case.news_artifact.sha256,
        "accepted_episode_count": 0,
        "swept_episode_count": 0,
        "retrieved_record_ids": [
            record.record_id for record in arm.retrieved_records
        ],
        "prediction_artifact": prediction_ref.artifact_path,
        "prediction_sha256": prediction_ref.sha256,
        "price_snapshot": {
            "source_name": "historical-fixture",
            "as_of": case.replay_cutoff_at.isoformat(),
            "allowed_through": (case.trade_date - timedelta(days=1)).isoformat(),
        },
        "model_config": {
            "configured_provider": arm.execution.llm_provider,
            "provider_class": "OpenAIResponsesProvider",
            "model": arm.execution.llm_model,
            "shadow_replay": {
                "arm_id": arm.arm_id,
                "features": arm.features.model_dump(mode="json"),
                "runner_protocol_version": arm.execution.runner_protocol_version,
                "llm_provider": arm.execution.llm_provider,
                "llm_model": arm.execution.llm_model,
                "prompt_version": arm.execution.prompt_version,
                "inference_config_sha256": arm.execution.inference_config_sha256,
            }
        },
    }
    context_ref = _ref(root, context_relative, context_payload)
    return [observation_ref, prediction_ref, context_ref]


def _case(
    root: Path,
    *,
    trade_day: date,
    split: str,
    calibration_index: int,
    dataset_kind: ShadowDatasetKind,
) -> ShadowReplayCase:
    case_id = f"CASE-{trade_day.isoformat()}"
    cutoff = datetime.combine(trade_day, datetime.min.time(), tzinfo=KST).replace(
        hour=8,
        minute=59,
        second=59,
    )
    snapshot = _snapshot(root, case_id, cutoff)
    legacy_snapshot = _legacy_snapshot(
        root,
        case_id,
        cutoff,
        production_snapshot=snapshot,
    )
    outcomes = _outcomes()
    news_ref = (
        _news_csv_ref(
            root,
            relative=f"fixtures/shadow/{case_id}/news.csv",
            trade_day=trade_day,
        )
        if dataset_kind == "SEALED_HISTORICAL_REPLAY"
        else _ref(
            root,
            f"fixtures/shadow/{case_id}/news.json",
            {"schema_version": "nslab.shadow_news_fixture.v1", "case_id": case_id},
        )
    )
    records = _retrieved_records(trade_day, cutoff)
    arms: list[ShadowArmObservation] = []
    for arm_id in SHADOW_ARM_FEATURES:
        execution = _execution(cutoff, dataset_kind=dataset_kind)
        selected_records = (
            []
            if arm_id == "A"
            else records[:1]
            if arm_id in {"B", "C", "D"}
            else records
        )
        arms.append(
            ShadowArmObservation(
                arm_id=arm_id,
                features=_features(arm_id),
                execution=execution,
                execution_attestation=ShadowArmAttestation(
                    issued_at=execution.completed_at,
                    key_id="0" * 16,
                    commitment_sha256=_ZERO_SHA,
                    signature=_ZERO_SHA,
                ),
                prediction_id=f"PRED-{case_id}-{arm_id}",
                candidates=_candidates(
                    arm_id,
                    split=split,
                    calibration_index=calibration_index,
                ),
                retrieved_records=selected_records,
                as_of_snapshot=(
                    None
                    if arm_id == "A"
                    else legacy_snapshot
                    if arm_id == "B"
                    else snapshot
                ),
                source_artifacts=(
                    [
                        _placeholder_ref(
                            f"fixtures/shadow/{case_id}/arm_{arm_id}.json"
                        ),
                        _placeholder_ref(
                            "runs/checkpoints/output_artifacts/"
                            f"RUN-{case_id}-{arm_id}/blind_prediction.json"
                        ),
                        _placeholder_ref(
                            f"runs/manifests/RUN-{case_id}-{arm_id}.json"
                        ),
                    ]
                    if dataset_kind == "SEALED_HISTORICAL_REPLAY"
                    else [
                        _placeholder_ref(
                            f"fixtures/shadow/{case_id}/arm_{arm_id}.json"
                        )
                    ]
                ),
                telemetry=_telemetry(arm_id),
            )
        )
    case = ShadowReplayCase(
        case_id=case_id,
        split=split,
        trade_date=trade_day,
        replay_cutoff_at=cutoff,
        news_artifact=news_ref,
        truth_artifact=_placeholder_ref(
            f"runs/shadow_evaluation/truth/{case_id}/pending.json"
        ),
        truth_attestation=ShadowTruthAttestation(
            issued_at=cutoff + timedelta(seconds=1),
            key_id="0" * 16,
            commitment_sha256=_ZERO_SHA,
            signature=_ZERO_SHA,
        ),
        postmortem_artifact=(
            _placeholder_ref(f"reports/{trade_day.isoformat()}_postmortem.json")
            if dataset_kind == "SEALED_HISTORICAL_REPLAY"
            else None
        ),
        outcome_universe_complete=True,
        outcomes=outcomes,
        known_relevant_record_ids=["LONG1", "R1"],
        negative_control_record_ids=["NEG1"],
        counterexample_record_ids=["COUNTER1"],
        long_tail_beneficiary_tickers=["T3"],
        arms=arms,
    )
    with patch.object(
        shadow_module,
        "now_kst",
        return_value=cutoff + timedelta(hours=12),
    ):
        case, truth_ref = seal_shadow_case_truth(
            root,
            case,
            key_value=_TRUTH_KEY,
        )
    if dataset_kind == "SEALED_HISTORICAL_REPLAY":
        postmortem_ref = _ref(
            root,
            f"reports/{trade_day.isoformat()}_postmortem.json",
            {
                "schema_version": "nslab.evaluation.v1",
                "trade_date": trade_day.isoformat(),
                "outcome_coverage_status": "FULL_MARKET_COMPLETE",
                "shadow_truth_sha256": truth_ref.sha256,
                "shadow_retrieval_truth": {
                    "schema_version": "nslab.shadow_retrieval_truth.v1",
                    "known_relevant_record_ids": ["LONG1", "R1"],
                    "negative_control_record_ids": ["NEG1"],
                    "counterexample_record_ids": ["COUNTER1"],
                    "long_tail_beneficiary_tickers": ["T3"],
                },
                "shadow_candidate_truth": {
                    "schema_version": "nslab.shadow_candidate_truth.v1",
                    "outcomes": [
                        {
                            "ticker": outcome.ticker,
                            "actual_theme_id": outcome.actual_theme_id,
                            "is_theme_leader": outcome.is_theme_leader,
                            "newsless": outcome.newsless,
                        }
                        for outcome in outcomes
                    ],
                },
            },
        )
        case = case.model_copy(update={"postmortem_artifact": postmortem_ref})
    finalized: list[ShadowArmObservation] = []
    for arm in case.arms:
        with patch.object(
            shadow_module,
            "now_kst",
            return_value=arm.execution.completed_at + timedelta(seconds=1),
        ):
            sealed_arm, observation_ref = seal_shadow_arm_observation(
                root,
                case,
                arm,
                key_value=_RUNNER_KEY,
            )
        if dataset_kind == "SEALED_HISTORICAL_REPLAY":
            source_refs = _historical_arm_source_refs(
                root,
                case=case,
                arm=sealed_arm,
                observation_ref=observation_ref,
            )
        else:
            source_refs = [observation_ref]
        finalized.append(
            sealed_arm.model_copy(update={"source_artifacts": source_refs})
        )
    return case.model_copy(update={"arms": finalized})


def _load_profiles(root: Path, *, measured: bool) -> list[ShadowLoadProfile]:
    profiles: list[ShadowLoadProfile] = []
    for count in (50_000, 200_000, 600_000):
        source_snapshot = (
            _snapshot(
                root,
                f"LOAD-{count}",
                datetime(2030, 2, 20, 8, 59, 59, tzinfo=KST),
                record_count=count,
            )
            if measured
            else None
        )
        sample_run_ids = (
            [f"LOAD-{count}-{index}" for index in range(5)]
            if measured
            else []
        )
        profiler_version = (
            "shadow_production_load_profiler.v1"
            if measured
            else "unmeasured.v1"
        )
        workload_payload = (
            {
                "schema_version": "nslab.shadow_load_workload.v1",
                "profiler_version": profiler_version,
                "record_count": count,
                "source_snapshot_id": source_snapshot.snapshot_id,
                "embedding_provider": "openai",
                "embedding_model": "llm_embedding:test:1536",
                "embedding_dimensions": 1536,
                "operations": ["pre_llm_retrieval", "daily_analysis"],
                "sample_count": len(sample_run_ids),
            }
            if source_snapshot is not None
            else None
        )
        workload_sha256 = (
            sha256_text(canonical_json(workload_payload))
            if workload_payload is not None
            else None
        )
        workload_artifact = (
            _ref(
                root,
                "runs/shadow_evaluation/load_profiles/"
                f"{count}/{source_snapshot.snapshot_id}/{workload_sha256}/"
                "workload.json",
                workload_payload,
            )
            if source_snapshot is not None
            and workload_payload is not None
            and workload_sha256 is not None
            else None
        )
        sample_started_at = (
            [
                datetime(2030, 2, 20, 9, index, 0, tzinfo=KST)
                for index in range(5)
            ]
            if measured
            else []
        )
        sample_completed_at = (
            [
                datetime(2030, 2, 20, 9, index, 1, tzinfo=KST)
                for index in range(5)
            ]
            if measured
            else []
        )
        sample_artifacts = (
            [
                _ref(
                    root,
                    "runs/shadow_evaluation/load_profiles/"
                    f"{count}/{source_snapshot.snapshot_id}/{workload_sha256}/"
                    f"{run_id}.json",
                    {
                        "schema_version": "nslab.shadow_load_sample.v1",
                        "record_count": count,
                        "source_snapshot_id": source_snapshot.snapshot_id,
                        "workload_sha256": workload_sha256,
                        "run_id": run_id,
                        "started_at": sample_started_at[index].isoformat(),
                        "completed_at": sample_completed_at[index].isoformat(),
                        "pre_llm_latency_ms": 2_000.0,
                        "daily_analysis_latency_ms": 40_000.0,
                        "peak_memory_bytes": 1024 * 1024 * 1024,
                        "online_full_scan_count": 0,
                    },
                )
                for index, run_id in enumerate(sample_run_ids)
            ]
            if source_snapshot is not None and workload_sha256 is not None
            else []
        )
        relative = (
            "runs/shadow_evaluation/load_profiles/"
            f"{count}/{source_snapshot.snapshot_id}/{workload_sha256}/profile.json"
            if source_snapshot is not None and workload_sha256 is not None
            else f"fixtures/shadow/load_{count}.json"
        )
        placeholder = _placeholder_ref(relative) if measured else None
        profile = ShadowLoadProfile(
            record_count=count,
            measured=measured,
            production_shape=measured,
            real_embedding_provider=measured,
            embedding_dimensions=1536 if measured else 0,
            embedding_provider="openai" if measured else "unavailable",
            embedding_model="llm_embedding:test:1536" if measured else "unavailable",
            profiler_version=profiler_version,
            workload_sha256=workload_sha256,
            workload_artifact=workload_artifact,
            load_attestation=(
                ShadowLoadAttestation(
                    issued_at=max(sample_completed_at),
                    key_id="0" * 16,
                    commitment_sha256=_ZERO_SHA,
                    signature=_ZERO_SHA,
                )
                if measured
                else None
            ),
            sample_run_ids=sample_run_ids,
            sample_started_at=sample_started_at,
            sample_completed_at=sample_completed_at,
            sample_artifacts=sample_artifacts,
            pre_llm_latency_samples_ms=[2_000.0] * 5 if measured else [],
            daily_analysis_latency_samples_ms=[40_000.0] * 5 if measured else [],
            peak_memory_samples_bytes=[1024 * 1024 * 1024] * 5 if measured else [],
            online_full_scan_samples=[0] * 5 if measured else [],
            pre_llm_latency_ms=(
                _distribution(2_000.0) if measured else _empty_distribution()
            ),
            daily_analysis_latency_ms=(
                _distribution(40_000.0) if measured else _empty_distribution()
            ),
            peak_memory_bytes=1024 * 1024 * 1024 if measured else 0,
            online_full_scan_count=0,
            profile_artifact=placeholder,
            source_snapshot_manifest=(
                source_snapshot.snapshot_manifest
                if source_snapshot is not None
                else None
            ),
            source_snapshot_id=(
                source_snapshot.snapshot_id if source_snapshot is not None else None
            ),
            source_generation_sha256=(
                source_snapshot.source_generation_sha256
                if source_snapshot is not None
                else None
            ),
            corpus_manifest_sha256=(
                source_snapshot.corpus_manifest_sha256
                if source_snapshot is not None
                else None
            ),
            blocker_reason=None if measured else "real provider profile unavailable",
        )
        if measured:
            with patch.object(
                shadow_module,
                "now_kst",
                return_value=max(sample_completed_at) + timedelta(seconds=1),
            ):
                profile, _ = seal_shadow_load_profile(
                    root,
                    profile,
                    key_value=_RUNNER_KEY,
                )
        profiles.append(profile)
    return profiles


def _dataset(
    root: Path,
    *,
    measured_load: bool = True,
    dataset_kind: ShadowDatasetKind = "SYNTHETIC_CONTRACT",
) -> Path:
    write_json(
        root / "memory" / "record_index" / "manifest.json",
        {
            "schema_version": "nslab.record_index_manifest.v2",
            "record_hash_kind": "canonical_full_envelope_sha256",
            "generation_root_sha256": "2" * 64,
            "generation_history": {},
        },
    )
    calibration_dates = [date(2030, 1, 1) + timedelta(days=index) for index in range(20)]
    holdout_dates = [date(2030, 2, 1) + timedelta(days=index) for index in range(20)]
    with patch.object(shadow_module, "now_kst", return_value=_SEALED_AT):
        split, _ = seal_shadow_split(
            root,
            _split_plan(root),
            key_value=_SHADOW_KEY,
        )
    cases = [
        _case(
            root,
            trade_day=trade_day,
            split="CALIBRATION",
            calibration_index=index,
            dataset_kind=dataset_kind,
        )
        for index, trade_day in enumerate(calibration_dates)
    ]
    cases.extend(
        _case(
            root,
            trade_day=trade_day,
            split="HOLDOUT",
            calibration_index=index,
            dataset_kind=dataset_kind,
        )
        for index, trade_day in enumerate(holdout_dates)
    )
    unsigned_dataset = {
        "schema_version": "nslab.shadow_replay_dataset_unsigned.v1",
        "dataset_id": "SHADOW-DATASET-2030",
        "dataset_kind": dataset_kind,
        "split": split.model_dump(mode="json"),
        "cases": [case.model_dump(mode="json") for case in cases],
        "load_profiles": [
            profile.model_dump(mode="json")
            for profile in _load_profiles(root, measured=measured_load)
        ],
    }
    unsigned_path = root / "fixtures" / "shadow" / "generated_unsigned_dataset.json"
    write_json(unsigned_path, unsigned_dataset)
    with patch.object(shadow_module, "now_kst", return_value=_DATASET_CREATED_AT):
        _, path = seal_shadow_dataset(
            root,
            unsigned_path,
            key_value=_SHADOW_KEY,
            memory_index=(
                _TEST_MEMORY_INDEX
                if dataset_kind == "SEALED_HISTORICAL_REPLAY"
                else None
            ),
            price_source=(
                _TEST_PRICE_SOURCE
                if dataset_kind == "SEALED_HISTORICAL_REPLAY"
                else None
            ),
        )
    return path


def _split_plan(root: Path) -> Path:
    path = root / "fixtures" / "shadow" / "split_plan.json"
    write_json(
        path,
        {
            "schema_version": "nslab.shadow_dataset_split_plan.v1",
            "build_start": "2029-01-01",
            "build_end": "2029-12-31",
            "calibration_start": "2030-01-01",
            "calibration_end": "2030-01-20",
            "holdout_start": "2030-02-01",
            "holdout_end": "2030-02-20",
            "calibration_dates": [
                (date(2030, 1, 1) + timedelta(days=index)).isoformat()
                for index in range(20)
            ],
            "holdout_dates": [
                (date(2030, 2, 1) + timedelta(days=index)).isoformat()
                for index in range(20)
            ],
        },
    )
    return path


def _unsigned_dataset(
    root: Path,
    *,
    dataset_kind: ShadowDatasetKind = "SYNTHETIC_CONTRACT",
) -> Path:
    sealed = read_json(_dataset(root, dataset_kind=dataset_kind))
    sealed.pop("dataset_attestation")
    sealed.pop("created_at")
    sealed["schema_version"] = "nslab.shadow_replay_dataset_unsigned.v1"
    path = root / "fixtures" / "shadow" / "unsigned_dataset.json"
    write_json(path, sealed)
    return path


def test_shadow_split_seal_is_content_addressed_and_authenticated(
    tmp_path: Path,
) -> None:
    with patch.object(shadow_module, "now_kst", return_value=_SEALED_AT):
        split, path = seal_shadow_split(
            tmp_path,
            _split_plan(tmp_path),
            key_value=_SHADOW_KEY,
        )
    assert path.name == "split_manifest.json"
    assert split.sealed_at == _SEALED_AT
    assert split.split_manifest.sha256 == file_sha256(path)
    assert verify_shadow_split_attestation(
        split.pre_registration_attestation,
        split_payload=shadow_split_commitment_payload(split),
        key_value=_SHADOW_KEY,
    )

    with patch.object(
        shadow_module,
        "now_kst",
        return_value=datetime(2030, 1, 1, 0, 0, 0, tzinfo=KST),
    ), pytest.raises(ValueError, match="before calibration starts"):
        seal_shadow_split(
            tmp_path / "late",
            _split_plan(tmp_path / "late"),
            key_value=_SHADOW_KEY,
        )

    with pytest.raises(ValueError, match="escapes the project root"):
        seal_shadow_split(
            tmp_path / "project",
            _split_plan(tmp_path / "outside"),
            key_value=_SHADOW_KEY,
        )


def test_shadow_dataset_seal_authenticates_the_full_source_closure(
    tmp_path: Path,
) -> None:
    with patch.object(shadow_module, "now_kst", return_value=_DATASET_CREATED_AT):
        dataset, path = seal_shadow_dataset(
            tmp_path,
            _unsigned_dataset(tmp_path),
            key_value=_SHADOW_KEY,
        )

    assert path.name == "source_dataset.json"
    assert dataset.created_at == _DATASET_CREATED_AT
    assert dataset.dataset_attestation.commitment_sha256
    assert read_json(path) == dataset.model_dump(mode="json")

def test_shadow_evaluation_builds_recomputable_a_to_f_holdout(tmp_path: Path) -> None:
    dataset_path = _dataset(tmp_path)

    result = ShadowReplayEvaluator(
        tmp_path, pre_registration_key=_SHADOW_KEY
    ).evaluate(dataset_path)
    inspection = ShadowReplayEvaluator(
        tmp_path, pre_registration_key=_SHADOW_KEY
    ).inspect(result.manifest_path)
    metrics = {item.arm_id: item for item in result.manifest.arm_metrics}

    assert inspection["passed"] is True
    assert result.manifest.production_ready is False
    assert result.manifest.exit_gate.passed is False
    assert result.manifest.exit_gate.checks[
        "sealed_historical_replay_execution"
    ] is False
    assert metrics["B"].candidate_recall_at_20.value == 0.0
    assert metrics["E"].candidate_recall_at_20.value == 1.0
    assert metrics["F"].candidate_recall_at_20.value == 1.0
    assert metrics["B"].brier_score == 0.75
    assert metrics["E"].brier_score == 0.0
    assert metrics["F"].newsless_hallucination_rate.value is None
    assert metrics["E"].known_relevant_record_recall.value == 1.0
    assert metrics["E"].negative_control_inclusion_rate.value == 1.0
    assert metrics["E"].counterexample_inclusion_rate.value == 1.0


def test_shadow_evaluation_preserves_unmeasured_production_blockers(
    tmp_path: Path,
) -> None:
    dataset_path = _dataset(tmp_path, measured_load=False)

    result = ShadowReplayEvaluator(
        tmp_path, pre_registration_key=_SHADOW_KEY
    ).evaluate(dataset_path)

    assert result.manifest.production_ready is False
    assert result.manifest.exit_gate.checks[
        "production_load_profiles_50k_200k_600k"
    ] is False
    assert "production_load_profiles_50k_200k_600k" in (
        result.manifest.exit_gate.blockers
    )


def test_shadow_historical_replay_passes_with_verified_source_closure(
    tmp_path: Path,
) -> None:
    result = ShadowReplayEvaluator(
        tmp_path,
        pre_registration_key=_SHADOW_KEY,
        memory_index=_TEST_MEMORY_INDEX,
        price_source=_TEST_PRICE_SOURCE,
    ).evaluate(
        _dataset(
            tmp_path,
            dataset_kind="SEALED_HISTORICAL_REPLAY",
        )
    )

    assert result.manifest.production_ready is True
    assert result.manifest.exit_gate.passed is True
    assert result.manifest.exit_gate.checks[
        "sealed_historical_replay_execution"
    ] is True
    assert result.manifest.exit_gate.checks[
        "actual_a_to_f_source_closure_verified"
    ] is True


def test_shadow_historical_source_closure_rejects_lookahead_or_future_news(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = _dataset(
        tmp_path,
        dataset_kind="SEALED_HISTORICAL_REPLAY",
    )
    monkeypatch.setattr(
        shadow_module,
        "audit_lookahead",
        lambda root, memory_index=None: {
            "passed": False,
            "findings": ["future evidence"],
            "checked_manifests": 240,
        },
    )
    with pytest.raises(ValueError, match="historical_lookahead_audit_failed"):
        ShadowReplayEvaluator(
            tmp_path,
            pre_registration_key=_SHADOW_KEY,
            memory_index=_TEST_MEMORY_INDEX,
            price_source=_TEST_PRICE_SOURCE,
        ).evaluate(dataset_path)

    monkeypatch.setattr(
        shadow_module,
        "audit_lookahead",
        lambda root, memory_index=None: {
            "passed": True,
            "findings": [],
            "checked_manifests": 240,
        },
    )
    dataset = read_json(dataset_path)
    news_path = tmp_path / dataset["cases"][0]["news_artifact"]["artifact_path"]
    news_path.write_text(
        "date,time,title,body\n2099-01-01,08:00:00,Future leak,Future body\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="shadow_news_cutoff_after_row"):
        ShadowReplayEvaluator(
            tmp_path,
            pre_registration_key=_SHADOW_KEY,
            memory_index=_TEST_MEMORY_INDEX,
            price_source=_TEST_PRICE_SOURCE,
        ).evaluate(dataset_path)


def test_shadow_historical_source_closure_checks_every_arm_source_hash(
    tmp_path: Path,
) -> None:
    dataset_path = _dataset(
        tmp_path,
        dataset_kind="SEALED_HISTORICAL_REPLAY",
    )
    dataset = read_json(dataset_path)
    prediction_ref = dataset["cases"][0]["arms"][0]["source_artifacts"][1]
    prediction_path = tmp_path / prediction_ref["artifact_path"]
    prediction = read_json(prediction_path)
    prediction["candidates"][0]["thesis"] = "coherent semantic rewrite"
    write_json(prediction_path, prediction)

    with pytest.raises(ValueError, match="shadow_source_hash_mismatch"):
        ShadowReplayEvaluator(
            tmp_path,
            pre_registration_key=_SHADOW_KEY,
            memory_index=_TEST_MEMORY_INDEX,
            price_source=_TEST_PRICE_SOURCE,
        ).evaluate(dataset_path)


def test_shadow_historical_seal_rejects_mock_provider_attestation(
    tmp_path: Path,
) -> None:
    unsigned_path = _unsigned_dataset(
        tmp_path,
        dataset_kind="SEALED_HISTORICAL_REPLAY",
    )
    unsigned = read_json(unsigned_path)
    context_ref = unsigned["cases"][0]["arms"][0]["source_artifacts"][2]
    context_path = tmp_path / context_ref["artifact_path"]
    context = read_json(context_path)
    context["model_config"]["provider_class"] = "MockLLMProvider"
    write_json(context_path, context)
    context_ref["sha256"] = file_sha256(context_path)
    write_json(unsigned_path, unsigned)

    with patch.object(shadow_module, "now_kst", return_value=_DATASET_CREATED_AT), \
        pytest.raises(ValueError, match="provider_attestation_mismatch"):
        seal_shadow_dataset(
            tmp_path,
            unsigned_path,
            key_value=_SHADOW_KEY,
            memory_index=_TEST_MEMORY_INDEX,
            price_source=_TEST_PRICE_SOURCE,
        )

    context["model_config"]["provider_class"] = "AttackerProductionProvider"
    write_json(context_path, context)
    context_ref["sha256"] = file_sha256(context_path)
    write_json(unsigned_path, unsigned)
    with patch.object(shadow_module, "now_kst", return_value=_DATASET_CREATED_AT), \
        pytest.raises(ValueError, match="provider_attestation_mismatch"):
        seal_shadow_dataset(
            tmp_path,
            unsigned_path,
            key_value=_SHADOW_KEY,
            memory_index=_TEST_MEMORY_INDEX,
            price_source=_TEST_PRICE_SOURCE,
        )


def test_shadow_historical_provider_gate_rejects_structural_fakes(
    tmp_path: Path,
) -> None:
    dataset = ShadowReplayDataset.model_validate(
        read_json(
            _dataset(
                tmp_path,
                dataset_kind="SEALED_HISTORICAL_REPLAY",
            )
        )
    )
    evaluator = ShadowReplayEvaluator(
        tmp_path,
        pre_registration_key=_SHADOW_KEY,
        price_source=_TEST_PRICE_SOURCE,
    )

    assert _REAL_PRODUCTION_PROVIDER_ERRORS(evaluator, dataset) == [
        "shadow_real_llm_provider_required",
        "shadow_real_price_provider_required",
    ]

    class AttackerOpenAIProvider(OpenAIResponsesProvider):
        pass

    class AttackerStockWebPriceSource(StockWebPriceSource):
        pass

    subclass_evaluator = ShadowReplayEvaluator(
        tmp_path,
        pre_registration_key=_SHADOW_KEY,
        llm_provider=AttackerOpenAIProvider(
            model="gpt-shadow-test",
            api_key="test-only",
        ),
        price_source=AttackerStockWebPriceSource(tmp_path),
    )
    assert _REAL_PRODUCTION_PROVIDER_ERRORS(subclass_evaluator, dataset) == [
        "shadow_real_llm_provider_required",
        "shadow_real_price_provider_required",
    ]


def test_shadow_load_profile_rejects_percentile_not_derived_from_samples(
    tmp_path: Path,
) -> None:
    payload = _load_profiles(tmp_path, measured=True)[0].model_dump(mode="json")
    payload["pre_llm_latency_samples_ms"][0] = 1_999.0

    with pytest.raises(ValueError, match="summaries do not match raw samples"):
        ShadowLoadProfile.model_validate(payload)

    payload = _load_profiles(tmp_path, measured=True)[0].model_dump(mode="json")
    payload["online_full_scan_samples"][0] = -1
    with pytest.raises(ValueError, match="scan samples must be non-negative"):
        ShadowLoadProfile.model_validate(payload)

    payload = _load_profiles(tmp_path, measured=True)[0].model_dump(mode="json")
    payload["daily_analysis_latency_samples_ms"].extend([0.0] * 100)
    payload["daily_analysis_latency_ms"] = NumericDistribution(
        count=105,
        minimum=0.0,
        mean=40_000.0 * 5 / 105,
        p50=0.0,
        p95=0.0,
        p99=40_000.0,
        maximum=40_000.0,
    ).model_dump(mode="json")
    with pytest.raises(ValueError, match="execution ledger is invalid"):
        ShadowLoadProfile.model_validate(payload)


def test_shadow_historical_truth_and_candidate_claims_are_source_projected(
    tmp_path: Path,
) -> None:
    dataset = ShadowReplayDataset.model_validate(
        read_json(
            _dataset(
                tmp_path,
                dataset_kind="SEALED_HISTORICAL_REPLAY",
            )
        )
    )
    case = dataset.cases[1]
    tampered_outcomes = [
        outcome.model_copy(update={"newsless": False})
        if outcome.ticker == "T2"
        else outcome
        for outcome in case.outcomes
    ]
    assert case.postmortem_artifact is not None
    postmortem_path = tmp_path / case.postmortem_artifact.artifact_path
    postmortem = read_json(postmortem_path)
    postmortem["shadow_candidate_truth"]["outcomes"] = [
        {
            "ticker": outcome.ticker,
            "actual_theme_id": outcome.actual_theme_id,
            "is_theme_leader": outcome.is_theme_leader,
            "newsless": outcome.newsless,
        }
        for outcome in tampered_outcomes
    ]
    write_json(postmortem_path, postmortem)
    tampered_case = case.model_copy(
        update={
            "outcomes": tampered_outcomes,
            "postmortem_artifact": case.postmortem_artifact.model_copy(
                update={"sha256": file_sha256(postmortem_path)}
            ),
        }
    )
    case_errors = ShadowReplayEvaluator(
        tmp_path,
        pre_registration_key=_SHADOW_KEY,
        price_source=_TEST_PRICE_SOURCE,
    )._historical_case_source_errors(tampered_case)
    assert any("shadow_truth_attestation_invalid" in item for item in case_errors)
    assert not any("shadow_postmortem_contract_invalid" in item for item in case_errors)

    arm = case.arms[1]
    assert arm.candidates[0].claims_news_cause is True
    tampered_arm = arm.model_copy(
        update={
            "candidates": [
                arm.candidates[0].model_copy(update={"claims_news_cause": False})
            ]
        }
    )
    arm_errors = ShadowReplayEvaluator(
        tmp_path,
        pre_registration_key=_SHADOW_KEY,
    )._historical_arm_source_errors(case, tampered_arm)
    assert any(
        "shadow_prediction_candidate_projection_mismatch" in item
        for item in arm_errors
    )


def test_shadow_arm_telemetry_rewrite_invalidates_runner_attestation(
    tmp_path: Path,
) -> None:
    dataset = ShadowReplayDataset.model_validate(read_json(_dataset(tmp_path)))
    case = dataset.cases[0]
    arm = case.arms[4]
    tampered_arm = arm.model_copy(
        update={
            "telemetry": arm.telemetry.model_copy(
                update={"pre_llm_latency_ms": 0.0}
            )
        }
    )
    payload = shadow_arm_source_payload(case, tampered_arm)
    relative = (
        "runs/shadow_evaluation/arm_observations/"
        f"{case.case_id}/{arm.arm_id}/"
        f"{payload['observation_sha256']}.json"
    )
    source_ref = _ref(tmp_path, relative, payload)
    tampered_arm = tampered_arm.model_copy(
        update={
            "source_artifacts": [source_ref, *tampered_arm.source_artifacts[1:]]
        }
    )
    tampered_case = case.model_copy(
        update={
            "arms": [
                tampered_arm if item.arm_id == arm.arm_id else item
                for item in case.arms
            ]
        }
    )

    errors = ShadowReplayEvaluator(
        tmp_path,
        pre_registration_key=_SHADOW_KEY,
    )._case_source_errors(tampered_case, snapshot_inspections={})

    assert any("shadow_arm_attestation_invalid" in item for item in errors)


def test_shadow_source_attestation_producers_reject_backdating(
    tmp_path: Path,
) -> None:
    dataset = ShadowReplayDataset.model_validate(read_json(_dataset(tmp_path)))
    case = dataset.cases[0]
    arm = case.arms[0]

    with patch.object(
        shadow_module,
        "now_kst",
        return_value=arm.execution.completed_at + timedelta(minutes=6),
    ), pytest.raises(ValueError, match="within five minutes"):
        seal_shadow_arm_observation(
            tmp_path,
            case,
            arm,
            key_value=_RUNNER_KEY,
        )

    with patch.object(
        shadow_module,
        "now_kst",
        return_value=case.replay_cutoff_at,
    ), pytest.raises(ValueError, match="attested after the replay cutoff"):
        seal_shadow_case_truth(
            tmp_path,
            case,
            key_value=_TRUTH_KEY,
        )

    load_profile = dataset.load_profiles[0]
    latest_completed = max(load_profile.sample_completed_at)
    with patch.object(
        shadow_module,
        "now_kst",
        return_value=latest_completed + timedelta(minutes=6),
    ), pytest.raises(ValueError, match="within five minutes"):
        seal_shadow_load_profile(
            tmp_path,
            load_profile,
            key_value=_RUNNER_KEY,
        )


def test_shadow_dataset_cannot_predate_load_samples(
    tmp_path: Path,
) -> None:
    payload = read_json(_dataset(tmp_path))
    profile = payload["load_profiles"][0]
    future_started = [f"2040-01-01T00:0{index}:00+09:00" for index in range(5)]
    future_completed = [f"2040-01-01T00:0{index}:01+09:00" for index in range(5)]
    profile["sample_started_at"] = future_started
    profile["sample_completed_at"] = future_completed
    profile["load_attestation"]["issued_at"] = "2040-01-01T00:04:02+09:00"

    with pytest.raises(ValueError, match="cannot predate load samples"):
        ShadowReplayDataset.model_validate(payload)


def test_shadow_dataset_seal_rejects_coherent_load_sample_rewrite(
    tmp_path: Path,
) -> None:
    unsigned_path = _unsigned_dataset(tmp_path)
    unsigned = read_json(unsigned_path)
    profile = unsigned["load_profiles"][0]
    sample_ref = profile["sample_artifacts"][0]
    sample_path = tmp_path / sample_ref["artifact_path"]
    sample = read_json(sample_path)
    sample["pre_llm_latency_ms"] = 1.0
    write_json(sample_path, sample)
    sample_ref["sha256"] = file_sha256(sample_path)

    profile_path = tmp_path / profile["profile_artifact"]["artifact_path"]
    observed_profile = read_json(profile_path)
    observed_profile["sample_artifacts"][0]["sha256"] = sample_ref["sha256"]
    write_json(profile_path, observed_profile)
    profile["profile_artifact"]["sha256"] = file_sha256(profile_path)
    write_json(unsigned_path, unsigned)

    with patch.object(shadow_module, "now_kst", return_value=_DATASET_CREATED_AT), \
        pytest.raises(ValueError, match="shadow_load_attestation_invalid"):
        seal_shadow_dataset(
            tmp_path,
            unsigned_path,
            key_value=_SHADOW_KEY,
        )


def test_shadow_dataset_seal_rejects_coherent_load_workload_rewrite(
    tmp_path: Path,
) -> None:
    unsigned_path = _unsigned_dataset(tmp_path)
    unsigned = read_json(unsigned_path)
    profile = unsigned["load_profiles"][0]
    workload_ref = profile["workload_artifact"]
    workload_path = tmp_path / workload_ref["artifact_path"]
    workload = read_json(workload_path)
    workload["operations"] = ["attacker_noop"]
    write_json(workload_path, workload)
    workload_ref["sha256"] = file_sha256(workload_path)

    profile_path = tmp_path / profile["profile_artifact"]["artifact_path"]
    observed_profile = read_json(profile_path)
    observed_profile["workload_artifact"]["sha256"] = workload_ref["sha256"]
    write_json(profile_path, observed_profile)
    profile["profile_artifact"]["sha256"] = file_sha256(profile_path)
    write_json(unsigned_path, unsigned)

    with patch.object(shadow_module, "now_kst", return_value=_DATASET_CREATED_AT), \
        pytest.raises(ValueError, match="shadow_load_attestation_invalid"):
        seal_shadow_dataset(
            tmp_path,
            unsigned_path,
            key_value=_SHADOW_KEY,
        )


def test_shadow_inspector_rejects_coherent_case_result_tamper(tmp_path: Path) -> None:
    result = ShadowReplayEvaluator(
        tmp_path, pre_registration_key=_SHADOW_KEY
    ).evaluate(_dataset(tmp_path))
    manifest = read_json(result.manifest_path)
    case_path = tmp_path / manifest["case_results"]["artifact_path"]
    rows = [json.loads(line) for line in case_path.read_text(encoding="utf-8").splitlines()]
    rows[0]["candidate_recall_5_numerator"] += 1
    case_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    manifest["case_results"]["sha256"] = file_sha256(case_path)
    write_json(result.manifest_path, manifest)

    inspection = ShadowReplayEvaluator(
        tmp_path, pre_registration_key=_SHADOW_KEY
    ).inspect(result.manifest_path)

    assert inspection["passed"] is False
    assert "shadow_case_results_recomputed_mismatch" in inspection["errors"]


def test_shadow_evaluation_rejects_snapshot_manifest_drift(tmp_path: Path) -> None:
    dataset_path = _dataset(tmp_path)
    dataset = read_json(dataset_path)
    snapshot_ref = dataset["cases"][0]["arms"][1]["as_of_snapshot"][
        "snapshot_manifest"
    ]
    snapshot_path = tmp_path / snapshot_ref["artifact_path"]
    payload = read_json(snapshot_path)
    payload["snapshot_id"] = "SNAP-ATTACKER"
    write_json(snapshot_path, payload)
    snapshot_ref["sha256"] = file_sha256(snapshot_path)
    write_json(dataset_path, dataset)

    with pytest.raises(ValueError, match="snapshot_manifest_recomputed_mismatch"):
        ShadowReplayEvaluator(
            tmp_path, pre_registration_key=_SHADOW_KEY
        ).evaluate(dataset_path)


def test_shadow_evaluation_requires_deep_production_snapshot_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = _dataset(tmp_path)
    monkeypatch.setattr(
        shadow_module,
        "inspect_memory_snapshot",
        lambda root, snapshot_id: {
            "passed": False,
            "production_ready": False,
            "snapshot_id": snapshot_id,
        },
    )

    with pytest.raises(ValueError, match="production_snapshot_inspection_failed"):
        ShadowReplayEvaluator(
            tmp_path,
            pre_registration_key=_SHADOW_KEY,
        ).evaluate(dataset_path)


def test_shadow_legacy_records_must_match_the_production_snapshot_database(
    tmp_path: Path,
) -> None:
    trade_day = date(2030, 2, 1)
    case = _case(
        tmp_path,
        trade_day=trade_day,
        split="HOLDOUT",
        calibration_index=0,
        dataset_kind="SYNTHETIC_CONTRACT",
    )
    arm = case.arms[1]
    production_snapshot = case.arms[2].as_of_snapshot
    assert production_snapshot is not None
    manifest = MemoryCellSnapshotManifest.model_validate(
        read_json(tmp_path / production_snapshot.snapshot_manifest.artifact_path)
    )
    database_path = tmp_path / "fixtures" / "shadow" / "projection.duckdb"
    database_path.parent.mkdir(parents=True, exist_ok=True)
    record = arm.retrieved_records[0]
    connection = duckdb.connect(str(database_path))
    try:
        connection.execute(
            """
            CREATE TABLE records (
                record_id VARCHAR,
                independent_unit_id VARCHAR,
                record_type VARCHAR,
                memory_lanes VARCHAR,
                evidence_polarity VARCHAR,
                trade_date DATE,
                available_from VARCHAR,
                regime_cluster VARCHAR
            )
            """
        )
        connection.execute(
            "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                record.record_id,
                record.independent_unit_id,
                record.record_type,
                json.dumps(record.memory_lanes),
                record.evidence_polarity,
                record.trade_date,
                record.available_from.isoformat(),
                record.regime_cluster,
            ],
        )
    finally:
        connection.close()
    database_ref = ArtifactReference(
        artifact_path=database_path.relative_to(tmp_path).as_posix(),
        sha256=file_sha256(database_path),
        item_count=1,
    )
    manifest = manifest.model_copy(update={"database": database_ref})

    assert _REAL_PRODUCTION_RETRIEVED_RECORD_ERRORS(
        tmp_path,
        case=case,
        arm=arm,
        manifest=manifest,
    ) == []

    tampered = arm.model_copy(
        update={
            "retrieved_records": [
                record.model_copy(update={"ticker": "ATTACKER-LONG-TAIL"})
            ]
        }
    )
    assert "shadow_snapshot_record_projection_mismatch" in (
        _REAL_PRODUCTION_RETRIEVED_RECORD_ERRORS(
            tmp_path,
            case=case,
            arm=tampered,
            manifest=manifest,
        )[0]
    )


def test_shadow_evaluation_rejects_wrong_split_pre_registration_key(
    tmp_path: Path,
) -> None:
    dataset_path = _dataset(tmp_path)

    with pytest.raises(
        ValueError,
        match="shadow_split_pre_registration_attestation_invalid",
    ):
        ShadowReplayEvaluator(
            tmp_path,
            pre_registration_key="attacker-shadow-evaluation-key-32-bytes-minimum",
        ).evaluate(dataset_path)


def test_shadow_dataset_rejects_missing_declared_holdout_date(tmp_path: Path) -> None:
    dataset_path = _dataset(tmp_path)
    payload = read_json(dataset_path)
    payload["cases"] = payload["cases"][:-1]
    write_json(dataset_path, payload)

    with pytest.raises(ValueError, match="holdout date coverage is incomplete"):
        ShadowReplayEvaluator(
            tmp_path, pre_registration_key=_SHADOW_KEY
        ).evaluate(dataset_path)


def test_shadow_dataset_rejects_cross_arm_snapshot_or_execution_drift(
    tmp_path: Path,
) -> None:
    payload = read_json(_dataset(tmp_path))
    snapshot_drift = json.loads(json.dumps(payload))
    snapshot_drift["cases"][0]["arms"][2]["as_of_snapshot"]["snapshot_id"] = (
        "SNAP-OTHER"
    )
    with pytest.raises(ValueError, match="C-F arms must share"):
        ShadowReplayDataset.model_validate(snapshot_drift)

    execution_drift = json.loads(json.dumps(payload))
    execution_drift["cases"][0]["arms"][5]["execution"]["prompt_version"] = (
        "shadow_attacker_prompt.v1"
    )
    with pytest.raises(ValueError, match="one execution contract"):
        ShadowReplayDataset.model_validate(execution_drift)

    predated = json.loads(json.dumps(payload))
    for arm in predated["cases"][0]["arms"]:
        arm["execution"]["started_at"] = "2018-01-01T00:00:00+09:00"
    with pytest.raises(ValueError, match="cannot predate split sealing"):
        ShadowReplayDataset.model_validate(predated)


def test_shadow_evaluator_requires_canonical_sealed_dataset_path(
    tmp_path: Path,
) -> None:
    sealed_path = _dataset(tmp_path)
    copied_path = tmp_path / "fixtures" / "shadow" / "copied_dataset.json"
    write_json(copied_path, read_json(sealed_path))

    with pytest.raises(ValueError, match="dataset path is not canonical"):
        ShadowReplayEvaluator(
            tmp_path,
            pre_registration_key=_SHADOW_KEY,
        ).evaluate(copied_path)


def test_shadow_cli_build_and_inspect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unsigned_dataset_path = _unsigned_dataset(tmp_path)
    seal_times = iter((_SEALED_AT, _DATASET_CREATED_AT))
    monkeypatch.setattr(shadow_module, "now_kst", lambda: next(seal_times))
    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: Settings(
            project_root=tmp_path,
            dotenv_values={"NSLAB_SHADOW_EVALUATION_HMAC_KEY": _SHADOW_KEY},
        ),
    )

    sealed = CliRunner().invoke(
        app,
        [
            "memory",
            "seal-shadow-split",
            _split_plan(tmp_path).relative_to(tmp_path).as_posix(),
        ],
    )
    assert sealed.exit_code == 0, sealed.output
    assert json.loads(sealed.output)["split"]["pre_registration_attestation"]

    dataset_sealed = CliRunner().invoke(
        app,
        [
            "memory",
            "seal-shadow-dataset",
            unsigned_dataset_path.relative_to(tmp_path).as_posix(),
        ],
    )
    assert dataset_sealed.exit_code == 0, dataset_sealed.output
    dataset_path = tmp_path / json.loads(dataset_sealed.output)["dataset_path"]

    built = CliRunner().invoke(
        app,
        ["memory", "evaluate-shadow", dataset_path.relative_to(tmp_path).as_posix()],
    )
    assert built.exit_code == 0, built.output
    built_payload = json.loads(built.output)
    assert built_payload["production_ready"] is False

    inspected = CliRunner().invoke(
        app,
        ["memory", "inspect-shadow", built_payload["manifest_path"]],
    )
    assert inspected.exit_code == 0, inspected.output
    assert json.loads(inspected.output)["passed"] is True


def test_shadow_readiness_reports_actual_prerequisites_without_claiming_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: Settings(project_root=tmp_path),
    )

    report = shadow_replay_readiness(tmp_path)
    command = CliRunner().invoke(app, ["memory", "shadow-readiness"])

    assert report["ready"] is False
    assert report["paired_historical_day_count"] == 0
    assert "minimum_calibration_and_holdout_days" in report["blockers"]
    assert "actual_a_to_f_source_closure_available" in report["blockers"]
    assert command.exit_code == 1
    assert json.loads(command.output)["ready"] is False
