from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

import news_scalping_lab.evaluation.quality_runtime as quality_runtime_module
import news_scalping_lab.inference.analyzer as analyzer_module
import news_scalping_lab.prices.factory as price_factory_module
import news_scalping_lab.prices.stock_web as stock_web_module
from news_scalping_lab.brain.category_index import build_category_brain_index
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.contracts.models import BlindPrediction, BrainManifest
from news_scalping_lab.contracts.quality_evaluation import quality_full_profile
from news_scalping_lab.evaluation.quality_runtime import (
    predict_runtime_variants,
    prepare_quality_runtime_selection,
    score_runtime_variants,
)
from news_scalping_lab.evaluation.replay_snapshot import build_shadow_as_of_snapshot
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    ReplayAvailabilityOverride,
    active_memory_snapshot_manifest,
)
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.records.models import BrainRecordEnvelope, CompiledBrainClaim
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import (
    AsyncEmbeddingProviderAdapter,
    DeterministicHashEmbeddingProvider,
)
from news_scalping_lab.utils import KST, canonical_json, file_sha256, sha256_text, write_json

T = TypeVar("T", bound=BaseModel)


class _EmbeddingBackend:
    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return DeterministicHashEmbeddingProvider().embed_texts(texts)


def _embedding_provider() -> AsyncEmbeddingProviderAdapter:
    adapter = AsyncEmbeddingProviderAdapter(
        _EmbeddingBackend(),
        embedding_method="llm_embedding:test:quality-score",
        production_capability_attested=True,
    )
    adapter.dimensions = DeterministicHashEmbeddingProvider.dimensions
    return adapter


class _CanonicalTickerMockLLM(DeterministicMockLLMProvider):
    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[T],
        purpose: str,
    ) -> T:
        observed = await super().generate_structured(
            prompt=prompt,
            response_model=response_model,
            purpose=purpose,
        )
        if not isinstance(observed, BlindPrediction):
            return observed
        candidates = [
            candidate.model_copy(update={"ticker": f"{candidate.rank:06d}"})
            for candidate in observed.candidates
        ]
        return observed.model_copy(update={"candidates": candidates})  # type: ignore[return-value]


class _SealedPriceSource:
    source_name = "quality-score-test"
    source_ref = "fixture://quality-score/d-minus-one"
    source_revision_sha256 = "a" * 64

    def get_blind_snapshot_universe(self, *, through: date) -> list[PriceRecord]:
        return [
            PriceRecord(
                ticker=f"{index:06d}",
                trade_date=through,
                open=100.0,
                high=100.0,
                low=100.0,
                close=100.0,
                volume=1.0,
                amount=100.0,
                market_cap=1_000.0,
                listed_shares=10.0,
            )
            for index in range(1, 6)
        ]


@pytest.mark.asyncio
async def test_quality_score_runs_after_complete_real_prediction_closure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(project_root=tmp_path)
    settings.limits.open_world_cluster_batch_size = 1
    settings.limits.novelty_cluster_batch_size = 1
    ensure_project_dirs(settings)
    snapshot_id = await asyncio.to_thread(_activate_evaluation_snapshot, tmp_path)
    await asyncio.to_thread(
        _write_evaluation_brain,
        tmp_path,
        settings=settings,
        snapshot_id=snapshot_id,
    )
    source_selection = _write_source_selection(tmp_path)
    prepared = prepare_quality_runtime_selection(
        tmp_path,
        source_selection_path=source_selection,
        split="CALIBRATION",
        scope="FULL_SPLIT",
        price_source=_SealedPriceSource(),
    )

    def forbid_prediction_time_price_access(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("prediction/scoring reopened the privileged price source")

    monkeypatch.setattr(
        quality_runtime_module,
        "create_price_source",
        forbid_prediction_time_price_access,
        raising=False,
    )
    monkeypatch.setattr(
        analyzer_module,
        "create_price_source",
        forbid_prediction_time_price_access,
    )
    monkeypatch.setattr(
        price_factory_module,
        "create_price_source",
        forbid_prediction_time_price_access,
    )
    for method_name in (
        "__init__",
        "get_blind_snapshot_universe",
        "_known_tickers",
        "_iter_records",
    ):
        monkeypatch.setattr(
            stock_web_module.StockWebPriceSource,
            method_name,
            forbid_prediction_time_price_access,
        )
    llm = _CanonicalTickerMockLLM(
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort or ""),
    )
    embedding = _embedding_provider()
    monkeypatch.setattr(
        quality_runtime_module,
        "create_llm_provider",
        lambda _settings: llm,
    )
    monkeypatch.setattr(
        quality_runtime_module,
        "create_configured_embedding_provider",
        lambda *_args, **_kwargs: embedding,
    )
    profile = quality_full_profile(
        provider=settings.llm_provider,
        model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort or ""),
    )

    predicted = await predict_runtime_variants(
        tmp_path,
        settings=settings,
        blind_selection_path=prepared.blind_selection_path,
        profile=profile,
    )
    paired = quality_runtime_module.PairedPredictionManifest.model_validate(
        quality_runtime_module.read_json(predicted.manifest_path)
    )
    v1_seal = next(seal for seal in paired.seals if seal.variant_id == "V1")
    context_path = tmp_path / v1_seal.context_manifest.artifact_path
    context = quality_runtime_module.ContextManifest.model_validate(
        quality_runtime_module.read_json(context_path)
    )
    shared_path = tmp_path / str(context.shared_pre_retrieval_context_artifact)
    shared_context = quality_runtime_module.SharedPreRetrievalContext.model_validate(
        quality_runtime_module.read_json(shared_path)
    )
    daily_path = tmp_path / str(context.daily_memory_context_artifact)
    graph_path = tmp_path / str(context.beneficiary_graph_artifact)
    daily_payload = json.loads(daily_path.read_bytes())
    compact_path = tmp_path / str(
        daily_payload["compact_final_context"]["artifact_path"]
    )
    safe_buffers = {
        path.resolve(): path.read_bytes()
        for path in (daily_path, graph_path, compact_path)
    }
    read_counts = dict.fromkeys(safe_buffers, 0)
    original_read_bytes = Path.read_bytes

    def swapping_phase7_bytes(candidate: Path) -> bytes:
        target = candidate.resolve()
        if target in safe_buffers:
            read_counts[target] += 1
            if read_counts[target] > 1:
                return b'{"coordinated_swap":{"D_day_outcome":true}}\n'
            return safe_buffers[target]
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_phase7_bytes)
    quality_runtime_module._verify_final_synthesis_contract_and_shared_digest(
        root=tmp_path,
        context=context,
        shared_context=shared_context,
    )
    assert read_counts == dict.fromkeys(safe_buffers, 1)
    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)
    outcome_reference = prepared.outcome_selection.cases[0].outcome_ledger
    outcome_path = tmp_path / outcome_reference.artifact_path
    outcome_bytes = outcome_path.read_bytes()
    outcome_read_count = 0

    def swapping_outcome_bytes(candidate: Path) -> bytes:
        nonlocal outcome_read_count
        if candidate.resolve() == outcome_path.resolve():
            outcome_read_count += 1
            if outcome_read_count > 1:
                return b'{"ticker":"999999","D_day_outcome":true}\n'
            return outcome_bytes
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_outcome_bytes)
    result = score_runtime_variants(
        tmp_path,
        paired_prediction_manifest_path=predicted.manifest_path,
        outcome_selection_path=prepared.outcome_selection_path,
    )
    assert outcome_read_count == 1
    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)

    assert predicted.manifest.all_predictions_sealed is True
    assert predicted.manifest.prediction_code_version.endswith(".v2")
    assert set(predicted.manifest.shared_preparation_ledgers) == {"CASE-SCORE"}
    for seal in predicted.manifest.seals:
        assert (
            seal.d_minus_one_payload_sha256
            != seal.d_minus_one_consumed_payload_sha256
        )
        assert seal.d_minus_one_projection_requested_ticker_count == (
            seal.d_minus_one_projection_snapshot_count
            + seal.d_minus_one_projection_missing_ticker_count
        )
        assert seal.d_minus_one_projection_snapshot_count < 5
    assert result.report["quality_evaluation_status"] == "PREDICTIVELY_EVALUATED"
    assert result.report["prediction_seal_count"] == 2
    assert result.report["market_metrics"]["V0"]["evaluation_universe_count"] == 5
    assert result.report["safety"]["forbidden_shared_key_count"] == 0
    shared_accounting = result.report["shared_stage_accounting"][
        "observed_shared_once"
    ]
    assert shared_accounting["build_count"] == 1
    assert shared_accounting["cache_load_count"] == 0
    assert shared_accounting["elapsed_accounting_status"] == "EXACT"
    v0_efficiency = result.report["efficiency_observations_non_blocking"]["V0"]
    assert "wall_clock_seconds" not in v0_efficiency
    assert v0_efficiency["wall_clock_accounting_status"] == "EXACT"
    assert v0_efficiency["wall_clock_lower_bound_seconds"] == (
        v0_efficiency["wall_clock_upper_bound_seconds"]
    )
    assert result.report_path.is_file()
    assert "## Date Results" in result.markdown_path.read_text(encoding="utf-8")


def _activate_evaluation_snapshot(root: Path) -> str:
    payload = {
        "record_type": "supervised_direct_event_case",
        "ticker": "000001",
        "company_name": "Fixture issuer",
        "title": "supply agreement confirmed",
        "event_id": "EVENT-1",
        "response_class": "POSITIVE",
        "high_return_pct": 12.0,
        "label_quality": "verified",
    }
    digest = sha256_text(canonical_json(payload))
    available_from = datetime(2029, 1, 2, tzinfo=KST)
    record = BrainRecordEnvelope(
        record_id="EP-20290101__REC-1",
        record_type="supervised_direct_event_case",
        episode_id="EP-20290101",
        trade_date=date(2029, 1, 1),
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        status="supported",
        confidence_label="high",
        provenance_source_ids=["SRC-RECORD-1"],
        raw_payload_sha256=digest,
        normalized_payload_sha256=digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )
    future_payload = {
        **payload,
        "event_id": "EVENT-FUTURE",
        "title": "future fixture event",
    }
    future_digest = sha256_text(canonical_json(future_payload))
    future_record = BrainRecordEnvelope(
        record_id="EP-20300201__REC-1",
        record_type="supervised_direct_event_case",
        episode_id="EP-20300201",
        trade_date=date(2030, 2, 1),
        available_from=datetime(2030, 2, 2, tzinfo=KST),
        training_target="direct_event_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        status="supported",
        confidence_label="high",
        provenance_source_ids=["SRC-FUTURE-1"],
        raw_payload_sha256=future_digest,
        normalized_payload_sha256=future_digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=future_payload,
    )
    records_path = root / "memory" / "records" / "EP-20290101.jsonl"
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text(record.model_dump_json() + "\n", encoding="utf-8")
    (records_path.parent / "EP-20300201.jsonl").write_text(
        future_record.model_dump_json() + "\n",
        encoding="utf-8",
    )
    BrainRecordStore(root).rebuild_indexes()
    index = ProductionMemoryIndex(
        root,
        embedding_provider=_embedding_provider(),
        production=True,
    )
    index.build(as_of=datetime(2030, 3, 1, tzinfo=KST))
    source = index.resolve_snapshot(cutoff_at=datetime(2030, 3, 1, tzinfo=KST))
    replay = build_shadow_as_of_snapshot(
        root,
        memory_index=index,
        build_cutoff=datetime(2030, 1, 1, tzinfo=KST),
        source_snapshot_id=source.snapshot_id,
        holdout_record_ids=set(),
        replay_availability_by_episode={
            record.episode_id: ReplayAvailabilityOverride(
                episode_id=record.episode_id,
                source_trade_date=record.trade_date,
                replay_available_from=available_from,
                derivation="QUALITY_SCORE_INTEGRATION",
            ),
            future_record.episode_id: ReplayAvailabilityOverride(
                episode_id=future_record.episode_id,
                source_trade_date=future_record.trade_date,
                replay_available_from=future_record.available_from,
                derivation="QUALITY_SCORE_INTEGRATION",
            ),
        },
    )
    index.activate_verified_evaluation_snapshot(
        replay.memory_snapshot,
        receipt_path=replay.receipt_path,
    )
    return replay.memory_snapshot.snapshot_id


def _write_evaluation_brain(
    root: Path,
    *,
    settings: Settings,
    snapshot_id: str,
) -> None:
    memory_snapshot = active_memory_snapshot_manifest(root)
    assert memory_snapshot is not None
    assert memory_snapshot.snapshot_id == snapshot_id
    claim = CompiledBrainClaim(
        claim_id="CLAIM-QUALITY-SCORE",
        category="single_event",
        statement="Cutoff-safe issuer events support candidate comparison.",
        mechanism="disclosure to issuer response",
        scope="fixture",
        supporting_record_ids=["EP-20290101__REC-1"],
        supporting_episode_ids=["EP-20290101"],
        positive_case_count=1,
        confidence_label="high",
        status="supported",
        available_from=datetime(2029, 1, 2, tzinfo=KST),
    )
    brain_version = "brain-quality-score"
    _index_manifest, index_path = build_category_brain_index(
        root,
        brain_version=brain_version,
        brain_record_cutoff_at=datetime(2030, 1, 1, tzinfo=KST),
        claims=[claim],
        embedding_provider=_embedding_provider(),
    )
    compiled_claims_text = claim.model_dump_json() + "\n"
    manifest = BrainManifest(
        brain_version=brain_version,
        created_at=datetime(2030, 1, 1, tzinfo=KST),
        build_mode="llm-full",
        production_eligible=True,
        llm_provider=settings.llm_provider,
        llm_model=settings.llm.model,
        reasoning_effort=str(settings.llm.reasoning_effort or ""),
        last_full_rebuild_at=datetime(2030, 1, 1, tzinfo=KST),
        accepted_episode_count=1,
        covered_episode_count=1,
        covered_episode_ids=["EP-20290101"],
        claim_ids=[claim.claim_id],
        compiled_claim_ids=[claim.claim_id],
        compiled_claim_count=1,
        compiled_claims_sha256=sha256_text(compiled_claims_text),
        source_hashes={"record:EP-20290101__REC-1": "5" * 64},
        brain_record_cutoff_at=datetime(2030, 1, 1, tzinfo=KST),
        production_memory_snapshot_id=snapshot_id,
        production_memory_corpus_sha256=memory_snapshot.corpus_manifest_sha256,
        production_memory_source_generation_sha256=(
            memory_snapshot.source_generation_sha256
        ),
        production_memory_as_of_cutoff=memory_snapshot.as_of_cutoff,
        category_brain_index_manifest_artifact=(
            index_path.relative_to(root).as_posix()
        ),
        category_brain_index_manifest_sha256=file_sha256(index_path),
        coverage_complete=True,
    )
    current = root / "brain" / "current"
    snapshot = root / "brain" / "snapshots" / brain_version
    coverage = {
        "brain_version": brain_version,
        "coverage_scope": "EVALUATION_REPLAY_BUILD",
        "covered_episode_ids": ["EP-20290101"],
    }
    for directory in (current, snapshot):
        write_json(
            directory / "brain_manifest.json",
            manifest.model_dump(mode="json"),
        )
        write_json(directory / "coverage_manifest.json", coverage)
        (directory / "compiled_claims.jsonl").write_bytes(
            compiled_claims_text.encode("utf-8")
        )
    (root / "brain" / "HEAD").write_text(brain_version, encoding="utf-8")


def _write_source_selection(root: Path) -> Path:
    episode = root / "research" / "episodes" / "CASE-SCORE"
    episode.mkdir(parents=True, exist_ok=True)
    normalized = episode / "normalized.json"
    source = episode / "source.jsonl"
    outcome = episode / "outcome.jsonl"
    write_json(
        normalized,
        {
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
        },
    )
    source.write_text(
        '{"available_before_cutoff":true,"source_id":"SRC-1",'
        '"published_at_kst":"2030-01-10T08:00:00+09:00",'
        '"title":"Issuer 000001 contract","body":"Confirmed agreement."}\n',
        encoding="utf-8",
    )
    outcome.write_text(
        "".join(
            canonical_json(
                {
                    "outcome_row_id": f"OUT-{index:06d}",
                    "snapshot_date": "2030-01-10",
                    "ticker": f"{index:06d}",
                    "data_quality_status": "clean",
                    "label_quality": "verified",
                    "quarantined": False,
                    "tradable": True,
                    "high_return_pct": float(31 - index),
                    "high_return_rank": index,
                    "upper_limit_touched": index == 1,
                    "upper_limit_closed": index == 1,
                    "upper_limit_released": False,
                    "corporate_action_warning": False,
                    "new_listing_or_no_reference": False,
                }
            )
            + "\n"
            for index in range(1, 6)
        ),
        encoding="utf-8",
    )
    selection = root / "source_selection.json"
    write_json(
        selection,
        {
            "schema_version": "nslab.semantic_upgrade_split_selection.v1",
            "cases": [
                {
                    "episode_id": "CASE-SCORE",
                    "trade_date": "2030-01-10",
                    "split": "CALIBRATION",
                    "normalized_index": {
                        "artifact_path": normalized.relative_to(root).as_posix(),
                        "sha256": file_sha256(normalized),
                    },
                    "source_ledger": {
                        "artifact_path": source.relative_to(root).as_posix(),
                        "sha256": file_sha256(source),
                    },
                    "outcome_ledger": {
                        "artifact_path": outcome.relative_to(root).as_posix(),
                        "sha256": file_sha256(outcome),
                    },
                }
            ],
        },
    )
    return selection
