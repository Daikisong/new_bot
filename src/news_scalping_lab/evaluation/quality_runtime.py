"""Quality-first blind runtime selection and scoring boundaries."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from news_scalping_lab.config import Settings
from news_scalping_lab.context.final_synthesis import (
    final_synthesis_context_contract_verified,
    final_synthesis_phase7_artifacts_compatible,
)
from news_scalping_lab.contracts.memory_context import DailyMemoryContext
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    ContextManifest,
    FinalSynthesisContextArtifact,
)
from news_scalping_lab.contracts.quality_evaluation import (
    BlindRuntimeCase,
    BlindRuntimeSelection,
    DMinusOnePromptProjection,
    PairedPredictionManifest,
    PredictionSeal,
    QualityArtifactReference,
    QualityEvaluationProfile,
    RuntimeOutcomeCase,
    RuntimeOutcomeSelection,
    SealedBlindCaseInputManifest,
    SharedDMinusOneContext,
    SharedDMinusOneSnapshot,
    SharedDownstreamDigest,
    SharedPreRetrievalContext,
    reject_forbidden_blind_payload_keys,
)
from news_scalping_lab.evaluation.quality_observations import (
    CitationClosureObservation,
    QualityCaseObservation,
    RetrievalCaseObservation,
    RuntimeEfficiencyObservation,
    SafetyObservation,
    SharedStageObservation,
)
from news_scalping_lab.evaluation.quality_reporting import (
    build_quality_score_report,
    render_quality_score,
)
from news_scalping_lab.evaluation.runtime_variant_shadow import (
    QUALITY_BRIER_POPULATION_POLICY_VERSION,
    QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    QUALITY_MARKET_UNIVERSE_POLICY_VERSION,
    _final_memory_citation_rate,
    _prediction_metrics,
    _runtime_counter_delta,
    _runtime_counter_snapshot,
    _trace_stats,
)
from news_scalping_lab.evaluation.shared_pre_retrieval import (
    SharedPreRetrievalBuildResult,
    build_shared_pre_retrieval_context,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.memory.index import active_memory_snapshot_manifest
from news_scalping_lab.prices.base import BlindSnapshotUniversePriceSource
from news_scalping_lab.retrieval.production_embedding import (
    create_configured_embedding_provider,
)
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    combine_kst,
    file_sha256,
    now_kst,
    parse_datetime,
    read_json,
    sha256_bytes,
    sha256_text,
    stable_id,
    write_json,
)

QUALITY_RUNTIME_SELECTION_VERSION = "nslab.quality_runtime_selection.v3"
BLIND_INPUT_CANONICALIZATION_VERSION = "quality_blind_news_csv.v2"
D_MINUS_ONE_CANONICALIZATION_VERSION = "quality_sealed_d_minus_one.v1"
BLIND_INPUT_ROOT = Path(
    "runs/semantic_brain_upgrade/quality_full/blind_inputs"
)
BLIND_SELECTION_ROOT = Path(
    "runs/semantic_brain_upgrade/quality_full/selections"
)
BLIND_SELECTION_FILENAME = "blind_runtime_selection.json"
PREDICTION_INPUT_BOUNDARY_VERSION = "SEALED_BLIND_INPUT.v3"
QUALITY_RUNTIME_PREDICTION_CODE_VERSION = (
    "nslab.quality_runtime_prediction_code.v2"
)
QUALITY_ATTEMPT_LEDGER_VERSION = "nslab.quality_prediction_attempt_ledger.v2"
QUALITY_RUNTIME_VARIANTS: tuple[Literal["V0", "V1"], ...] = ("V0", "V1")
QUALITY_RUNTIME_COUNTER_KEYS = (
    "logical_llm_call_count",
    "oauth_live_agent_call_count",
    "llm_checkpoint_hit_count",
    "oauth_cache_event_count",
    "llm_prompt_tokens_estimate",
    "llm_completion_tokens_estimate",
    "llm_retry_count",
    "llm_error_trace_count",
    "embedding_query_count",
    "embedding_text_count",
    "embedding_input_char_count",
    "new_llm_trace_count",
    "process_rss_before_bytes",
    "process_rss_after_bytes",
    "process_peak_working_set_bytes",
    "pre_llm_latency_seconds",
)
QUALITY_RUNTIME_GAUGE_KEYS = {
    "process_rss_before_bytes",
    "process_rss_after_bytes",
    "process_peak_working_set_bytes",
}
QualitySelectionScope = Literal["THREE_CASE", "FULL_SPLIT"]


@dataclass(frozen=True)
class QualityRuntimeSelectionResult:
    blind_selection: BlindRuntimeSelection
    blind_selection_path: Path
    outcome_selection: RuntimeOutcomeSelection
    outcome_selection_path: Path


@dataclass(frozen=True)
class BlindCaseNewsInput:
    news_csv_path: Path
    news_sha256: str
    cutoff_at: str
    row_count: int
    receipt_path: Path
    d_minus_one_context_path: Path
    d_minus_one_context_reference: QualityArtifactReference
    d_minus_one_context: SharedDMinusOneContext
    d_minus_one_payload_sha256: str


@dataclass(frozen=True)
class _PreparedQualityCase:
    episode_id: str
    trade_date: date
    split: Literal["CALIBRATION", "HOLDOUT"]
    cutoff_at: datetime
    cutoff_derivation: Literal[
        "NORMALIZED_INDEX",
        "TRADE_DATE_08_59_59_KST",
    ]
    normalized_index: QualityArtifactReference
    source_ledger: QualityArtifactReference
    outcome_ledger: QualityArtifactReference
    source_ledger_rows: tuple[dict[str, Any], ...]
    cutoff_safe_news_row_count: int


def prepare_quality_runtime_selection(
    root: Path,
    *,
    source_selection_path: Path,
    split: Literal["CALIBRATION", "HOLDOUT"],
    scope: QualitySelectionScope,
    price_source: BlindSnapshotUniversePriceSource,
) -> QualityRuntimeSelectionResult:
    """Project source cases into physically separate blind and outcome manifests."""

    root = root.resolve()
    source_selection_path = source_selection_path.resolve()
    source = _read_source_selection(source_selection_path)
    source_cases = [
        row
        for row in source["cases"]
        if isinstance(row, dict) and row.get("split") == split
    ]
    if not source_cases:
        raise ValueError(f"quality runtime source selection has no {split} cases")

    prepared = [
        _prepare_source_case(root, row=row, split=split) for row in source_cases
    ]
    ordered = sorted(
        prepared,
        key=lambda case: (
            case.cutoff_safe_news_row_count,
            case.trade_date,
            case.episode_id,
        ),
    )
    selected_prepared = _select_scope(ordered, scope=scope)
    selected = [
        (
            _seal_blind_case_input(
                root,
                prepared=case,
                price_source=price_source,
            ),
            RuntimeOutcomeCase(
                episode_id=case.episode_id,
                trade_date=case.trade_date,
                split=case.split,
                outcome_ledger=case.outcome_ledger,
            ),
        )
        for case in selected_prepared
    ]
    source_sha256 = file_sha256(source_selection_path)
    blind_payload = {
        "version": QUALITY_RUNTIME_SELECTION_VERSION,
        "source_selection_sha256": source_sha256,
        "split": split,
        "scope": scope,
        "cases": [case.model_dump(mode="json") for case, _outcome in selected],
    }
    selection_id = stable_id(
        "QSEL",
        canonical_json(blind_payload),
        length=20,
    )
    blind = BlindRuntimeSelection(
        selection_id=selection_id,
        source_selection_sha256=source_sha256,
        selection_policy=(
            "CUTOFF_SAFE_NEWS_ROW_MIN_LOWER_MEDIAN_MAX"
            if scope == "THREE_CASE"
            else "ALL_SOURCE_SPLIT_CASES"
        ),
        cases=[case for case, _outcome in selected],
    )
    output_dir = (
        root
        / "runs"
        / "semantic_brain_upgrade"
        / "quality_full"
        / "selections"
        / selection_id
    )
    blind_path = output_dir / "blind_runtime_selection.json"
    _write_immutable_bytes(
        blind_path,
        _pretty_json_bytes(blind.model_dump(mode="json")),
    )
    blind_sha256 = file_sha256(blind_path)
    outcome = RuntimeOutcomeSelection(
        selection_id=selection_id,
        blind_selection_sha256=blind_sha256,
        cases=[outcome for _case, outcome in selected],
    )
    outcome_path = output_dir / "runtime_outcome_selection.json"
    _write_immutable_bytes(
        outcome_path,
        _pretty_json_bytes(outcome.model_dump(mode="json")),
    )
    _verify_no_forbidden_prediction_keys(read_json(blind_path))
    return QualityRuntimeSelectionResult(
        blind_selection=blind,
        blind_selection_path=blind_path,
        outcome_selection=outcome,
        outcome_selection_path=outcome_path,
    )


def load_blind_runtime_selection(
    root: Path,
    path: Path,
) -> BlindRuntimeSelection:
    """Load the only selection shape accepted by the prediction process."""

    resolved = _resolve_blind_selection_path(root, path)
    payload = read_json(resolved)
    return _validate_blind_runtime_selection_payload(resolved, payload)


def _validate_blind_runtime_selection_payload(
    resolved: Path,
    payload: object,
) -> BlindRuntimeSelection:
    _verify_no_forbidden_prediction_keys(payload)
    selection = BlindRuntimeSelection.model_validate(payload)
    scope_by_policy: dict[str, QualitySelectionScope] = {
        "CUTOFF_SAFE_NEWS_ROW_MIN_LOWER_MEDIAN_MAX": "THREE_CASE",
        "ALL_SOURCE_SPLIT_CASES": "FULL_SPLIT",
    }
    scope = scope_by_policy.get(selection.selection_policy)
    splits = {case.split for case in selection.cases}
    if scope is None or not splits.issubset({"CALIBRATION", "HOLDOUT"}) or len(splits) != 1:
        raise ValueError("blind runtime selection policy identity is invalid")
    expected_selection_id = stable_id(
        "QSEL",
        canonical_json(
            {
                "version": QUALITY_RUNTIME_SELECTION_VERSION,
                "source_selection_sha256": selection.source_selection_sha256,
                "split": next(iter(splits)),
                "scope": scope,
                "cases": [
                    case.model_dump(mode="json") for case in selection.cases
                ],
            }
        ),
        length=20,
    )
    if (
        selection.selection_id != expected_selection_id
        or selection.selection_id != resolved.parent.name
    ):
        raise ValueError("blind runtime selection location is invalid")
    return selection


def load_runtime_outcome_selection(path: Path) -> RuntimeOutcomeSelection:
    return RuntimeOutcomeSelection.model_validate(read_json(path.resolve()))


def materialize_blind_case_news(
    root: Path,
    *,
    case: BlindRuntimeCase,
    output_dir: Path,
) -> BlindCaseNewsInput:
    """Load only a pre-sealed prediction-safe input package."""

    root = root.resolve()
    manifest_path = _resolve_blind_input_reference(
        root,
        case.blind_input_manifest,
    )
    manifest_payload = _read_verified_json_reference(
        case.blind_input_manifest,
        manifest_path,
    )
    _verify_no_forbidden_prediction_keys(manifest_payload)
    manifest = SealedBlindCaseInputManifest.model_validate(manifest_payload)
    _verify_sealed_input_location(
        manifest,
        manifest_reference=case.blind_input_manifest,
        manifest_path=manifest_path,
    )
    if (
        manifest.episode_id != case.episode_id
        or manifest.trade_date != case.trade_date
        or as_kst(manifest.cutoff_at) != as_kst(case.cutoff_at)
        or manifest.cutoff_safe_news_row_count
        != case.cutoff_safe_news_row_count
        or manifest.news_csv.sha256 != case.news_sha256
        or manifest.d_minus_one_context.sha256
        != case.d_minus_one_context_sha256
        or manifest.d_minus_one_payload_sha256
        != case.d_minus_one_payload_sha256
        or manifest.d_minus_one_candidate_universe_root_sha256
        != case.d_minus_one_candidate_universe_root_sha256
        or manifest.d_minus_one_snapshot_root_sha256
        != case.d_minus_one_snapshot_root_sha256
        or manifest.d_minus_one_source_revision_sha256
        != case.d_minus_one_source_revision_sha256
        or manifest.d_minus_one_snapshot_session_date
        != case.d_minus_one_snapshot_session_date
    ):
        raise ValueError("blind runtime sealed input identity mismatch")
    csv_path = _resolve_blind_input_reference(root, manifest.news_csv)
    if csv_path.parent != manifest_path.parent:
        raise ValueError("blind runtime sealed CSV escaped its input package")
    csv_bytes = _read_verified_bytes_reference(manifest.news_csv, csv_path)
    if sha256_bytes(csv_bytes) != case.news_sha256:
        raise ValueError("blind runtime sealed news hash mismatch")
    rows = _verify_sealed_news_csv(
        csv_bytes,
        cutoff_at=case.cutoff_at,
    )
    if len(rows) != case.cutoff_safe_news_row_count:
        raise ValueError("blind runtime news row count differs from sealed selection")
    d_minus_one_path = _resolve_blind_input_reference(
        root,
        manifest.d_minus_one_context,
    )
    if d_minus_one_path.parent != manifest_path.parent:
        raise ValueError("blind runtime sealed D-1 artifact escaped its input package")
    d_minus_one_payload = _read_verified_json_reference(
        manifest.d_minus_one_context,
        d_minus_one_path,
    )
    reject_forbidden_blind_payload_keys(d_minus_one_payload)
    d_minus_one_context = SharedDMinusOneContext.model_validate(
        d_minus_one_payload
    )
    d_minus_one_payload_sha256 = sha256_text(
        canonical_json(d_minus_one_context.model_dump(mode="json"))
    )
    if (
        d_minus_one_context.trade_date != case.trade_date
        or as_kst(d_minus_one_context.cutoff_at) != as_kst(case.cutoff_at)
        or d_minus_one_context.allowed_through
        != case.trade_date - timedelta(days=1)
        or d_minus_one_payload_sha256
        != manifest.d_minus_one_payload_sha256
        or d_minus_one_context.candidate_universe_root_sha256
        != manifest.d_minus_one_candidate_universe_root_sha256
        or d_minus_one_context.snapshot_root_sha256
        != manifest.d_minus_one_snapshot_root_sha256
        or d_minus_one_context.source_revision_sha256
        != manifest.d_minus_one_source_revision_sha256
        or d_minus_one_context.snapshot_session_date
        != manifest.d_minus_one_snapshot_session_date
    ):
        raise ValueError("blind runtime sealed D-1 identity mismatch")
    input_dir = output_dir.resolve() / "inputs"
    input_dir.mkdir(parents=True, exist_ok=True)
    materialized_csv_path = input_dir / f"{case.episode_id}.csv"
    _write_immutable_bytes(materialized_csv_path, csv_bytes)
    receipt = {
        "schema_version": "nslab.blind_case_news_input.v3",
        "prediction_input_boundary_version": PREDICTION_INPUT_BOUNDARY_VERSION,
        "episode_id": case.episode_id,
        "trade_date": case.trade_date.isoformat(),
        "cutoff_at": as_kst(case.cutoff_at).isoformat(),
        "blind_input_manifest_sha256": case.blind_input_manifest.sha256,
        "news_csv_sha256": case.news_sha256,
        "row_count": len(rows),
        "d_minus_one_context_sha256": manifest.d_minus_one_context.sha256,
        "d_minus_one_payload_sha256": d_minus_one_payload_sha256,
        "d_minus_one_candidate_universe_root_sha256": (
            d_minus_one_context.candidate_universe_root_sha256
        ),
        "d_minus_one_snapshot_root_sha256": (
            d_minus_one_context.snapshot_root_sha256
        ),
        "outcome_reference_count": 0,
        "forbidden_reference_count": 0,
    }
    receipt_path = input_dir / f"{case.episode_id}.receipt.json"
    _write_immutable_bytes(
        receipt_path,
        (canonical_json(receipt) + "\n").encode("utf-8"),
    )
    return BlindCaseNewsInput(
        news_csv_path=materialized_csv_path,
        news_sha256=case.news_sha256,
        cutoff_at=as_kst(case.cutoff_at).isoformat(),
        row_count=len(rows),
        receipt_path=receipt_path,
        d_minus_one_context_path=d_minus_one_path,
        d_minus_one_context_reference=manifest.d_minus_one_context,
        d_minus_one_context=d_minus_one_context,
        d_minus_one_payload_sha256=d_minus_one_payload_sha256,
    )


def _read_source_selection(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        != "nslab.semantic_upgrade_split_selection.v1"
        or not isinstance(payload.get("cases"), list)
    ):
        raise ValueError("semantic upgrade source selection is invalid")
    return payload


def _prepare_source_case(
    root: Path,
    *,
    row: dict[str, Any],
    split: Literal["CALIBRATION", "HOLDOUT"],
) -> _PreparedQualityCase:
    episode_id = row.get("episode_id")
    trade_date = row.get("trade_date")
    if not isinstance(episode_id, str) or not episode_id.strip():
        raise ValueError("quality runtime source case has no episode ID")
    normalized_index = _reference(row.get("normalized_index"))
    source_ledger = _reference(row.get("source_ledger"))
    outcome_ledger = _reference(row.get("outcome_ledger"))
    normalized_path = _resolve_reference(root, normalized_index)
    source_path = _resolve_reference(root, source_ledger)
    index = _read_verified_json_reference(normalized_index, normalized_path)
    source_rows = _read_verified_jsonl_reference(source_ledger, source_path)
    if not isinstance(index, dict) or str(index.get("trade_date")) != str(
        trade_date
    ):
        raise ValueError("quality runtime normalized index identity mismatch")
    cutoff_value = index.get("cutoff_at")
    parsed_trade_date = date.fromisoformat(str(trade_date))
    if isinstance(cutoff_value, str) and cutoff_value.strip():
        cutoff_at = as_kst(parse_datetime(cutoff_value))
        cutoff_derivation: Literal[
            "NORMALIZED_INDEX",
            "TRADE_DATE_08_59_59_KST",
        ] = "NORMALIZED_INDEX"
    else:
        cutoff_at = combine_kst(parsed_trade_date, "08:59:59")
        cutoff_derivation = "TRADE_DATE_08_59_59_KST"
    if cutoff_at.date() != parsed_trade_date:
        raise ValueError("quality runtime normalized cutoff and trade date differ")
    return _PreparedQualityCase(
        episode_id=episode_id,
        trade_date=parsed_trade_date,
        split=split,
        cutoff_at=cutoff_at,
        cutoff_derivation=cutoff_derivation,
        normalized_index=normalized_index,
        source_ledger=source_ledger,
        outcome_ledger=outcome_ledger,
        source_ledger_rows=tuple(source_rows),
        cutoff_safe_news_row_count=_cutoff_safe_row_count(source_rows),
    )


def _seal_blind_case_input(
    root: Path,
    *,
    prepared: _PreparedQualityCase,
    price_source: BlindSnapshotUniversePriceSource,
) -> BlindRuntimeCase:
    rows, source_row_ids = _cutoff_safe_news_rows(
        prepared.source_ledger_rows,
        cutoff_at=prepared.cutoff_at,
    )
    if len(rows) != prepared.cutoff_safe_news_row_count:
        raise ValueError("sealed blind input row count drifted during preparation")
    news_bytes = _canonical_news_csv_bytes(rows)
    news_sha256 = sha256_bytes(news_bytes)
    source_row_root_sha256 = sha256_text(canonical_json(source_row_ids))
    d_minus_one_context = _build_privileged_d_minus_one_context(
        price_source,
        trade_date=prepared.trade_date,
        cutoff_at=prepared.cutoff_at,
    )
    d_minus_one_payload_sha256 = sha256_text(
        canonical_json(d_minus_one_context.model_dump(mode="json"))
    )
    d_minus_one_bytes = (
        canonical_json(d_minus_one_context.model_dump(mode="json")) + "\n"
    ).encode("utf-8")
    d_minus_one_file_sha256 = sha256_bytes(d_minus_one_bytes)
    identity = {
        "schema_version": "nslab.sealed_blind_case_input_identity.v2",
        "episode_id": prepared.episode_id,
        "trade_date": prepared.trade_date.isoformat(),
        "cutoff_at": as_kst(prepared.cutoff_at).isoformat(),
        "news_sha256": news_sha256,
        "d_minus_one_context_sha256": d_minus_one_file_sha256,
        "d_minus_one_payload_sha256": d_minus_one_payload_sha256,
        "d_minus_one_candidate_universe_root_sha256": (
            d_minus_one_context.candidate_universe_root_sha256
        ),
        "d_minus_one_snapshot_root_sha256": (
            d_minus_one_context.snapshot_root_sha256
        ),
        "d_minus_one_source_revision_sha256": (
            d_minus_one_context.source_revision_sha256
        ),
        "d_minus_one_snapshot_session_date": (
            d_minus_one_context.snapshot_session_date.isoformat()
            if d_minus_one_context.snapshot_session_date is not None
            else None
        ),
        "source_row_root_sha256": source_row_root_sha256,
        "source_ledger_commitment_sha256": prepared.source_ledger.sha256,
        "source_metadata_commitment_sha256": prepared.normalized_index.sha256,
        "canonicalization_version": BLIND_INPUT_CANONICALIZATION_VERSION,
        "d_minus_one_canonicalization_version": (
            D_MINUS_ONE_CANONICALIZATION_VERSION
        ),
        "cutoff_derivation": prepared.cutoff_derivation,
    }
    input_id = stable_id("QINPUT", canonical_json(identity), length=20)
    output_dir = root / BLIND_INPUT_ROOT / input_id
    news_path = output_dir / "news.csv"
    d_minus_one_path = output_dir / "d_minus_one_safe_context.json"
    _write_immutable_bytes(news_path, news_bytes)
    _write_immutable_bytes(d_minus_one_path, d_minus_one_bytes)
    d_minus_one_reference = _path_reference(root, d_minus_one_path)
    manifest = SealedBlindCaseInputManifest(
        input_id=input_id,
        episode_id=prepared.episode_id,
        trade_date=prepared.trade_date,
        cutoff_at=prepared.cutoff_at,
        news_csv=_path_reference(root, news_path),
        d_minus_one_context=d_minus_one_reference,
        d_minus_one_payload_sha256=d_minus_one_payload_sha256,
        d_minus_one_candidate_universe_root_sha256=(
            d_minus_one_context.candidate_universe_root_sha256
        ),
        d_minus_one_snapshot_root_sha256=(
            d_minus_one_context.snapshot_root_sha256
        ),
        d_minus_one_source_revision_sha256=(
            d_minus_one_context.source_revision_sha256
        ),
        d_minus_one_snapshot_session_date=(
            d_minus_one_context.snapshot_session_date
        ),
        d_minus_one_canonicalization_version=(
            D_MINUS_ONE_CANONICALIZATION_VERSION
        ),
        cutoff_safe_news_row_count=len(rows),
        source_row_ids=source_row_ids,
        source_row_root_sha256=source_row_root_sha256,
        source_ledger_commitment_sha256=prepared.source_ledger.sha256,
        source_metadata_commitment_sha256=prepared.normalized_index.sha256,
        canonicalization_version=BLIND_INPUT_CANONICALIZATION_VERSION,
        cutoff_derivation=prepared.cutoff_derivation,
    )
    manifest_path = output_dir / "sealed_blind_case_input_manifest.json"
    _write_immutable_bytes(
        manifest_path,
        (canonical_json(manifest.model_dump(mode="json")) + "\n").encode("utf-8"),
    )
    return BlindRuntimeCase(
        episode_id=prepared.episode_id,
        trade_date=prepared.trade_date,
        split=prepared.split,
        cutoff_at=prepared.cutoff_at,
        blind_input_manifest=_path_reference(root, manifest_path),
        news_sha256=news_sha256,
        cutoff_safe_news_row_count=len(rows),
        d_minus_one_context_sha256=d_minus_one_reference.sha256,
        d_minus_one_payload_sha256=d_minus_one_payload_sha256,
        d_minus_one_candidate_universe_root_sha256=(
            d_minus_one_context.candidate_universe_root_sha256
        ),
        d_minus_one_snapshot_root_sha256=(
            d_minus_one_context.snapshot_root_sha256
        ),
        d_minus_one_source_revision_sha256=(
            d_minus_one_context.source_revision_sha256
        ),
        d_minus_one_snapshot_session_date=(
            d_minus_one_context.snapshot_session_date
        ),
    )


def _build_privileged_d_minus_one_context(
    price_source: BlindSnapshotUniversePriceSource,
    *,
    trade_date: date,
    cutoff_at: datetime,
) -> SharedDMinusOneContext:
    """Read the price repository only while preparing the sealed QINPUT."""

    allowed_through = trade_date - timedelta(days=1)
    raw_records = list(
        price_source.get_blind_snapshot_universe(through=allowed_through)
    )
    normalized: list[SharedDMinusOneSnapshot] = []
    for record in raw_records:
        ticker = record.ticker.strip().upper()
        if record.trade_date > allowed_through:
            raise ValueError(
                "privileged D-1 source returned a D-day or future snapshot"
            )
        normalized.append(
            SharedDMinusOneSnapshot(
                ticker=ticker,
                trade_date=record.trade_date,
                open=record.open,
                high=record.high,
                low=record.low,
                close=record.close,
                volume=record.volume,
                amount=record.amount,
                market_cap=record.market_cap,
                listed_shares=record.listed_shares,
            )
        )
    tickers = [row.ticker for row in normalized]
    if len(tickers) != len(set(tickers)):
        raise ValueError("privileged D-1 source returned duplicate ticker snapshots")
    snapshot_session_date = (
        max(row.trade_date for row in normalized) if normalized else None
    )
    sealed_snapshots = sorted(
        (
            row
            for row in normalized
            if row.trade_date == snapshot_session_date
        ),
        key=lambda row: row.ticker,
    )
    universe = [row.ticker for row in sealed_snapshots]
    snapshot_payload = [row.model_dump(mode="json") for row in sealed_snapshots]
    source_name = str(getattr(price_source, "source_name", "")).strip()
    if not source_name:
        source_name = price_source.__class__.__name__
    declared_source_ref = getattr(price_source, "source_ref", None)
    source_root = getattr(price_source, "root", None)
    source_ref = (
        declared_source_ref.strip()
        if isinstance(declared_source_ref, str) and declared_source_ref.strip()
        else source_root.as_posix()
        if isinstance(source_root, Path)
        else None
    )
    return SharedDMinusOneContext(
        status=(
            "D_MINUS_ONE_FIXED_UNIVERSE"
            if universe
            else "D_MINUS_ONE_FIXED_UNIVERSE_EMPTY"
        ),
        trade_date=trade_date,
        cutoff_at=as_kst(cutoff_at),
        allowed_through=allowed_through,
        source_name=source_name,
        source_ref=source_ref,
        snapshot_session_date=snapshot_session_date,
        source_revision_sha256=_price_source_revision_sha256(price_source),
        candidate_universe=universe,
        candidate_universe_root_sha256=sha256_text(canonical_json(universe)),
        snapshots=sealed_snapshots,
        snapshot_root_sha256=sha256_text(canonical_json(snapshot_payload)),
        skipped_tickers=[],
        sealed_snapshot_count=len(sealed_snapshots),
        privileged_source_snapshot_count=len(normalized),
        privileged_source_query_count=1,
        price_repository_access_count=0,
    )


def _price_source_revision_sha256(
    price_source: BlindSnapshotUniversePriceSource,
) -> str:
    declared = getattr(price_source, "source_revision_sha256", None)
    if isinstance(declared, str) and _is_sha256(declared):
        return declared
    source_root = getattr(price_source, "root", None)
    file_commitments: dict[str, str] = {}
    if isinstance(source_root, Path):
        atlas_root = (
            source_root / "atlas"
            if (source_root / "atlas").is_dir()
            else source_root
        )
        for name in ("manifest.json", "schema.json", "source_manifest.json"):
            path = atlas_root / name
            if path.is_file():
                file_commitments[name] = file_sha256(path)
    return sha256_text(
        canonical_json(
            {
                "schema_version": "nslab.price_source_revision.v1",
                "source_name": str(getattr(price_source, "source_name", "")),
                "source_class": (
                    f"{price_source.__class__.__module__}."
                    f"{price_source.__class__.__qualname__}"
                ),
                "file_commitments": file_commitments,
            }
        )
    )


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(
        character in "0123456789abcdef" for character in value
    )


def _select_scope(
    ordered: list[_PreparedQualityCase],
    *,
    scope: QualitySelectionScope,
) -> list[_PreparedQualityCase]:
    if scope == "FULL_SPLIT":
        return sorted(ordered, key=lambda case: case.trade_date)
    if len(ordered) < 3:
        raise ValueError("three-case quality selection requires at least three cases")
    indexes = (0, (len(ordered) - 1) // 2, len(ordered) - 1)
    selected = [ordered[index] for index in indexes]
    if len({case.episode_id for case in selected}) != 3:
        raise ValueError("three-case quality selection did not produce unique cases")
    return selected


def _reference(value: object) -> QualityArtifactReference:
    return QualityArtifactReference.model_validate(value)


def _resolve_reference(root: Path, reference: QualityArtifactReference) -> Path:
    path = Path(reference.artifact_path)
    if path.is_absolute():
        return path.resolve()
    if any(part == ".." for part in path.parts):
        raise ValueError("quality runtime artifact escapes the project root")
    # Evaluation projects intentionally junction immutable research data from the
    # source project. Keep the sealed logical path while file/hash checks follow
    # the junction at read time.
    return root.resolve() / path


def _verify_reference(
    reference: QualityArtifactReference,
    path: Path,
) -> None:
    if not path.is_file() or file_sha256(path) != reference.sha256:
        raise ValueError(f"quality runtime source artifact hash mismatch: {path}")


def _read_verified_json_reference(
    reference: QualityArtifactReference,
    path: Path,
    *,
    text_hash: bool = False,
) -> object:
    """Read, hash, and decode one immutable reference from the same byte buffer."""

    if not path.is_file():
        raise ValueError(f"quality runtime source artifact is missing: {path}")
    payload_bytes = path.read_bytes()
    try:
        payload_text = payload_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"quality runtime source artifact is invalid JSON: {path}") from exc
    normalized_text = payload_text.replace("\r\n", "\n").replace("\r", "\n")
    observed_sha256 = (
        sha256_text(normalized_text) if text_hash else sha256_bytes(payload_bytes)
    )
    if observed_sha256 != reference.sha256:
        raise ValueError(f"quality runtime source artifact hash mismatch: {path}")
    try:
        return json.loads(normalized_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"quality runtime source artifact is invalid JSON: {path}") from exc


def _read_verified_bytes_reference(
    reference: QualityArtifactReference,
    path: Path,
) -> bytes:
    if not path.is_file():
        raise ValueError(f"quality runtime source artifact is missing: {path}")
    payload_bytes = path.read_bytes()
    if sha256_bytes(payload_bytes) != reference.sha256:
        raise ValueError(f"quality runtime source artifact hash mismatch: {path}")
    return payload_bytes


def _read_verified_jsonl_reference(
    reference: QualityArtifactReference,
    path: Path,
) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"quality runtime source artifact is missing: {path}")
    payload_bytes = path.read_bytes()
    if sha256_bytes(payload_bytes) != reference.sha256:
        raise ValueError(f"quality runtime source artifact hash mismatch: {path}")
    try:
        payload_text = payload_bytes.decode("utf-8-sig")
        rows = [json.loads(line) for line in payload_text.splitlines() if line.strip()]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"quality runtime source artifact is invalid JSONL: {path}") from exc
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("quality runtime source ledger row must be an object")
    return rows


def _cutoff_safe_row_count(source_rows: Sequence[dict[str, Any]]) -> int:
    count = sum(row.get("available_before_cutoff") is True for row in source_rows)
    if count < 1:
        raise ValueError("quality runtime source ledger has no cutoff-safe news")
    return count


def _cutoff_safe_news_rows(
    source_rows: Sequence[dict[str, Any]],
    *,
    cutoff_at: datetime,
) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    source_row_ids: list[str] = []
    for row in source_rows:
        if row.get("available_before_cutoff") is not True:
            continue
        source_id = row.get("source_id")
        published_value = row.get("published_at_kst")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("blind runtime source row has no source ID")
        if not isinstance(published_value, str) or not published_value.strip():
            raise ValueError("blind runtime source row has no KST publication time")
        published_at = as_kst(parse_datetime(published_value))
        if published_at > as_kst(cutoff_at):
            raise ValueError("cutoff-safe source row is later than the sealed cutoff")
        source_row_ids.append(source_id.strip())
        rows.append(
            {
                "date": published_at.date().isoformat(),
                "time": published_at.timetz().replace(tzinfo=None).isoformat(),
                "title": str(row.get("title") or ""),
                "body": str(row.get("body") or ""),
            }
        )
    if not rows:
        raise ValueError("quality runtime source ledger has no cutoff-safe news")
    if len(source_row_ids) != len(set(source_row_ids)):
        raise ValueError("blind runtime source row IDs are duplicated")
    return rows, source_row_ids


def _canonical_news_csv_bytes(rows: list[dict[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        buffer,
        fieldnames=["date", "time", "title", "body"],
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8-sig")


def _verify_sealed_news_csv(
    payload_bytes: bytes,
    *,
    cutoff_at: datetime,
) -> list[dict[str, str]]:
    try:
        payload_text = payload_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("blind runtime sealed CSV encoding is invalid") from exc
    reader = csv.DictReader(io.StringIO(payload_text, newline=""))
    if reader.fieldnames != ["date", "time", "title", "body"]:
        raise ValueError("blind runtime sealed CSV schema is invalid")
    rows = [dict(row) for row in reader]
    for row in rows:
        try:
            published_at = as_kst(
                parse_datetime(f"{row['date']}T{row['time']}")
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("blind runtime sealed CSV time is invalid") from exc
        if published_at > as_kst(cutoff_at):
            raise ValueError("blind runtime sealed CSV contains future news")
    return rows


def _resolve_blind_input_reference(
    root: Path,
    reference: QualityArtifactReference,
) -> Path:
    logical = Path(reference.artifact_path)
    if logical.is_absolute() or any(part == ".." for part in logical.parts):
        raise ValueError("blind input reference must be project-relative")
    try:
        logical.relative_to(BLIND_INPUT_ROOT)
    except ValueError as exc:
        # Reject non-allowlisted logical paths before resolving or touching them.
        raise ValueError(
            "blind input reference is outside the sealed allowlist"
        ) from exc
    allowed_root = (root.resolve() / BLIND_INPUT_ROOT).resolve()
    resolved = (root.resolve() / logical).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError("blind input reference is outside the sealed allowlist") from exc
    return resolved


def _resolve_blind_selection_path(root: Path, path: Path) -> Path:
    logical = Path(path)
    if any(part == ".." for part in logical.parts):
        raise ValueError("blind selection path must not traverse parent directories")

    resolved_root = root.resolve()
    if logical.is_absolute():
        try:
            project_relative = logical.relative_to(resolved_root)
        except ValueError as exc:
            # Do not resolve or touch an absolute path outside the project.
            raise ValueError(
                "blind selection path is outside the sealed allowlist"
            ) from exc
    else:
        project_relative = logical

    try:
        selection_relative = project_relative.relative_to(BLIND_SELECTION_ROOT)
    except ValueError as exc:
        # Reject non-allowlisted logical paths before resolving or touching them.
        raise ValueError(
            "blind selection path is outside the sealed allowlist"
        ) from exc

    parts = selection_relative.parts
    selection_dir = parts[0] if parts else ""
    if (
        len(parts) != 2
        or parts[1] != BLIND_SELECTION_FILENAME
        or not selection_dir.startswith("QSEL-")
        or len(selection_dir) != 25
        or any(character not in "0123456789abcdef" for character in selection_dir[5:])
    ):
        raise ValueError("blind selection path is not a sealed blind selection")

    allowed_root = (resolved_root / BLIND_SELECTION_ROOT).resolve()
    resolved = (resolved_root / project_relative).resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            "blind selection path is outside the sealed allowlist"
        ) from exc
    return resolved


def _verify_sealed_input_location(
    manifest: SealedBlindCaseInputManifest,
    *,
    manifest_reference: QualityArtifactReference,
    manifest_path: Path,
) -> None:
    logical_manifest = Path(manifest_reference.artifact_path)
    logical_news = Path(manifest.news_csv.artifact_path)
    logical_d_minus_one = Path(manifest.d_minus_one_context.artifact_path)
    expected_input_id = stable_id(
        "QINPUT",
        canonical_json(
            {
                "schema_version": "nslab.sealed_blind_case_input_identity.v2",
                "episode_id": manifest.episode_id,
                "trade_date": manifest.trade_date.isoformat(),
                "cutoff_at": as_kst(manifest.cutoff_at).isoformat(),
                "news_sha256": manifest.news_csv.sha256,
                "d_minus_one_context_sha256": (
                    manifest.d_minus_one_context.sha256
                ),
                "d_minus_one_payload_sha256": (
                    manifest.d_minus_one_payload_sha256
                ),
                "d_minus_one_candidate_universe_root_sha256": (
                    manifest.d_minus_one_candidate_universe_root_sha256
                ),
                "d_minus_one_snapshot_root_sha256": (
                    manifest.d_minus_one_snapshot_root_sha256
                ),
                "d_minus_one_source_revision_sha256": (
                    manifest.d_minus_one_source_revision_sha256
                ),
                "d_minus_one_snapshot_session_date": (
                    manifest.d_minus_one_snapshot_session_date.isoformat()
                    if manifest.d_minus_one_snapshot_session_date is not None
                    else None
                ),
                "source_row_root_sha256": manifest.source_row_root_sha256,
                "source_ledger_commitment_sha256": (
                    manifest.source_ledger_commitment_sha256
                ),
                "source_metadata_commitment_sha256": (
                    manifest.source_metadata_commitment_sha256
                ),
                "canonicalization_version": manifest.canonicalization_version,
                "d_minus_one_canonicalization_version": (
                    manifest.d_minus_one_canonicalization_version
                ),
                "cutoff_derivation": manifest.cutoff_derivation,
            }
        ),
        length=20,
    )
    if (
        manifest.input_id != expected_input_id
        or
        logical_manifest.name != "sealed_blind_case_input_manifest.json"
        or logical_manifest.parent.name != manifest.input_id
        or manifest_path.parent.name != manifest.input_id
        or logical_news.name != "news.csv"
        or logical_news.parent.name != manifest.input_id
        or logical_d_minus_one.name != "d_minus_one_safe_context.json"
        or logical_d_minus_one.parent.name != manifest.input_id
    ):
        raise ValueError("sealed blind input package location is invalid")


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload)
    except FileExistsError as exc:
        if path.read_bytes() != payload:
            raise ValueError(
                f"immutable quality artifact conflict: {path}"
            ) from exc


def _write_json_atomic(path: Path, payload: object) -> None:
    encoded = _pretty_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        with temporary.open("xb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _pretty_json_bytes(payload: object) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _verify_no_forbidden_prediction_keys(payload: object) -> None:
    reject_forbidden_blind_payload_keys(payload)
    forbidden = {
        "outcome",
        "outcomes",
        "outcome_ledger",
        "truth",
        "truth_labels",
        "winner_census",
        "postmortem",
        "d_snapshot",
        "normalized_index",
        "normalized_episode_index",
        "source_ledger",
        "raw_block_names",
    }
    discovered: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                normalized = str(key).strip().casefold()
                if normalized in forbidden:
                    discovered.add(str(key))
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    if discovered:
        raise ValueError(
            "blind prediction selection contains forbidden outcome fields: "
            + ", ".join(sorted(discovered))
        )


def blind_selection_sha256(selection: BlindRuntimeSelection) -> str:
    return sha256_text(canonical_json(selection.model_dump(mode="json")))


def _candidate_universe_policy_sha256(
    *,
    settings: Settings,
    profile: QualityEvaluationProfile,
    case: BlindRuntimeCase,
    parsed_news_root_sha256: str,
    shared_context_sha256: str,
    brain_manifest: QualityArtifactReference,
    coverage_manifest: QualityArtifactReference,
    memory_snapshot_id: str,
    d_minus_one_context: QualityArtifactReference,
    d_minus_one_candidate_universe_root_sha256: str,
    d_minus_one_snapshot_root_sha256: str,
    d_minus_one_source_revision_sha256: str,
    d_minus_one_snapshot_session_date: date | None,
    d_minus_one_payload_sha256: str,
) -> str:
    payload = {
        "schema_version": "nslab.candidate_universe_policy_identity.v1",
        "policy": "OPEN_WORLD_FIRST_THEN_BUILD_SNAPSHOT_MEMORY.v1",
        "analysis_mode": "exhaustive",
        "evidence_policy": str(settings.evidence_policy),
        "event_cluster_fallback_policy": str(
            settings.event_cluster_fallback_policy
        ),
        "limits": settings.limits.model_dump(mode="json"),
        "profile": profile.model_dump(mode="json"),
        "case_id": case.episode_id,
        "trade_date": case.trade_date.isoformat(),
        "cutoff_at": as_kst(case.cutoff_at).isoformat(),
        "news_sha256": case.news_sha256,
        "parsed_news_root_sha256": parsed_news_root_sha256,
        "shared_context_sha256": shared_context_sha256,
        "brain_manifest_sha256": brain_manifest.sha256,
        "coverage_manifest_sha256": coverage_manifest.sha256,
        "memory_snapshot_id": memory_snapshot_id,
        "d_minus_one_context_sha256": d_minus_one_context.sha256,
        "d_minus_one_candidate_universe_root_sha256": (
            d_minus_one_candidate_universe_root_sha256
        ),
        "d_minus_one_snapshot_root_sha256": d_minus_one_snapshot_root_sha256,
        "d_minus_one_source_revision_sha256": (
            d_minus_one_source_revision_sha256
        ),
        "d_minus_one_snapshot_session_date": (
            d_minus_one_snapshot_session_date.isoformat()
            if d_minus_one_snapshot_session_date is not None
            else None
        ),
        "d_minus_one_payload_sha256": d_minus_one_payload_sha256,
        "runtime_retrieval_variant_excluded": True,
    }
    return sha256_text(canonical_json(payload))


def _variant_architecture_sha256(
    *,
    variant_id: str,
    profile: QualityEvaluationProfile,
    brain_manifest: QualityArtifactReference,
    coverage_manifest: QualityArtifactReference,
    memory_snapshot_id: str,
) -> str:
    runtime_variant = {"V0": "legacy", "V1": "v4"}.get(variant_id)
    if runtime_variant is None:
        raise ValueError(f"unsupported quality runtime variant: {variant_id}")
    return sha256_text(
        canonical_json(
            {
                "schema_version": "nslab.quality_variant_architecture.v1",
                "variant_id": variant_id,
                "runtime_retrieval_variant": runtime_variant,
                "profile": profile.model_dump(mode="json"),
                "brain_manifest": brain_manifest.model_dump(mode="json"),
                "coverage_manifest": coverage_manifest.model_dump(mode="json"),
                "memory_snapshot_id": memory_snapshot_id,
            }
        )
    )


@dataclass(frozen=True)
class QualityRuntimePredictionResult:
    manifest: PairedPredictionManifest
    manifest_path: Path


@dataclass(frozen=True)
class _PreparedPredictionCase:
    case: BlindRuntimeCase
    news_input: BlindCaseNewsInput
    cutoff_at: datetime
    shared: SharedPreRetrievalBuildResult
    shared_context_sha256: str
    shared_manifest_sha256: str
    shared_preparation_attempt: _SharedPreparationAttempt
    d_minus_one_reference: QualityArtifactReference
    d_minus_one_path: Path
    d_minus_one_context: SharedDMinusOneContext
    d_minus_one_payload_sha256: str


@dataclass(frozen=True)
class _QualityAttempt:
    attempt_number: int
    attempt_id: str
    start_path: Path
    started_at: datetime


@dataclass(frozen=True)
class _SharedPreparationAttempt:
    attempt_number: int
    attempt_id: str
    scope_id: str
    start_path: Path
    started_at: datetime


def _shared_preparation_scope_id(
    *,
    blind_selection: QualityArtifactReference,
    case: BlindRuntimeCase,
    profile: QualityEvaluationProfile,
) -> str:
    return stable_id(
        "QSHARED",
        canonical_json(
            {
                "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
                "blind_selection": blind_selection.model_dump(mode="json"),
                "case_id": case.episode_id,
                "blind_input_manifest": case.blind_input_manifest.model_dump(
                    mode="json"
                ),
                "profile": profile.model_dump(mode="json"),
            }
        ),
        length=20,
    )


def _begin_shared_preparation_attempt(
    root: Path,
    *,
    scope_id: str,
    case_id: str,
    runtime_before: dict[str, Any],
) -> _SharedPreparationAttempt:
    attempt_dir = (
        root
        / "runs"
        / "semantic_brain_upgrade"
        / "quality_full"
        / "shared_preparation_attempts"
        / scope_id
        / case_id
    )
    existing = sorted(attempt_dir.glob("*.start.json"))
    attempt_number = len(existing) + 1
    started_at = now_kst()
    attempt_id = stable_id(
        "QSHATT",
        scope_id,
        case_id,
        str(attempt_number),
        started_at.isoformat(),
        length=20,
    )
    start_path = attempt_dir / f"{attempt_number:04d}.start.json"
    serializable_before = {
        **runtime_before,
        "trace_files": sorted(str(value) for value in runtime_before["trace_files"]),
    }
    _write_immutable_bytes(
        start_path,
        _pretty_json_bytes(
            {
                "schema_version": "nslab.quality_shared_preparation_start.v1",
                "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
                "scope_id": scope_id,
                "case_id": case_id,
                "attempt_number": attempt_number,
                "attempt_id": attempt_id,
                "started_at": started_at.isoformat(),
                "runtime_before": serializable_before,
            }
        ),
    )
    return _SharedPreparationAttempt(
        attempt_number=attempt_number,
        attempt_id=attempt_id,
        scope_id=scope_id,
        start_path=start_path,
        started_at=started_at,
    )


def _complete_shared_preparation_attempt(
    attempt: _SharedPreparationAttempt,
    *,
    elapsed_seconds: float,
    runtime_metrics: dict[str, Any],
    cache_hit: bool,
    shared_context_sha256: str,
    shared_manifest_sha256: str,
) -> Path:
    completion_path = attempt.start_path.with_name(
        f"{attempt.attempt_number:04d}.completion.json"
    )
    _write_immutable_bytes(
        completion_path,
        _pretty_json_bytes(
            {
                "schema_version": "nslab.quality_shared_preparation_completion.v1",
                "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "completed_at": now_kst().isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "cache_hit": cache_hit,
                "shared_context_sha256": shared_context_sha256,
                "shared_manifest_sha256": shared_manifest_sha256,
                "runtime_metrics": runtime_metrics,
            }
        ),
    )
    return completion_path


def _trusted_shared_context_anchor_from_prediction_seals(
    root: Path,
    *,
    blind_selection: QualityArtifactReference,
    case: BlindRuntimeCase,
    profile: QualityEvaluationProfile,
) -> str | None:
    """Recover only a shared hash already committed by a separate QPRED seal."""

    prediction_root = (
        root
        / "runs"
        / "semantic_brain_upgrade"
        / "quality_full"
        / "predictions"
    )
    anchors: set[str] = set()
    for path in sorted(prediction_root.glob("QPRED-*/paired_prediction_manifest.json")):
        try:
            manifest = PairedPredictionManifest.model_validate(read_json(path))
        except (OSError, ValueError):
            continue
        if manifest.blind_selection != blind_selection or manifest.profile != profile:
            continue
        for seal in manifest.seals:
            if (
                seal.case_id == case.episode_id
                and seal.blind_input_manifest == case.blind_input_manifest
                and seal.news_sha256 == case.news_sha256
                and seal.d_minus_one_context_sha256
                == case.d_minus_one_context_sha256
                and seal.d_minus_one_source_revision_sha256
                == case.d_minus_one_source_revision_sha256
            ):
                anchors.add(seal.shared_context_sha256)
    if len(anchors) > 1:
        raise ValueError("conflicting external shared-context prediction seals")
    return next(iter(anchors), None)


def _begin_quality_attempt(
    root: Path,
    *,
    output_dir: Path,
    run_id: str,
    case_id: str,
    variant_id: str,
    runtime_before: dict[str, Any],
) -> _QualityAttempt:
    attempt_dir = output_dir / "attempts" / case_id / variant_id
    existing = sorted(attempt_dir.glob("*.start.json"))
    attempt_number = len(existing) + 1
    started_at = now_kst()
    attempt_id = stable_id(
        "QATT",
        run_id,
        case_id,
        variant_id,
        str(attempt_number),
        started_at.isoformat(),
        length=20,
    )
    start_path = attempt_dir / f"{attempt_number:04d}.start.json"
    serializable_before = {
        **runtime_before,
        "trace_files": sorted(str(value) for value in runtime_before["trace_files"]),
    }
    _write_immutable_bytes(
        start_path,
        _pretty_json_bytes(
            {
                "schema_version": "nslab.quality_prediction_attempt_start.v1",
                "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
                "run_id": run_id,
                "case_id": case_id,
                "variant_id": variant_id,
                "attempt_number": attempt_number,
                "attempt_id": attempt_id,
                "started_at": started_at.isoformat(),
                "runtime_before": serializable_before,
            }
        ),
    )
    return _QualityAttempt(
        attempt_number=attempt_number,
        attempt_id=attempt_id,
        start_path=start_path,
        started_at=started_at,
    )


def _complete_quality_attempt(
    attempt: _QualityAttempt,
    *,
    elapsed_seconds: float,
    runtime_metrics: dict[str, Any],
) -> Path:
    completion_path = attempt.start_path.with_name(
        f"{attempt.attempt_number:04d}.completion.json"
    )
    _write_immutable_bytes(
        completion_path,
        _pretty_json_bytes(
            {
                "schema_version": "nslab.quality_prediction_attempt_completion.v1",
                "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "completed_at": now_kst().isoformat(),
                "elapsed_seconds": elapsed_seconds,
                "runtime_metrics": runtime_metrics,
            }
        ),
    )
    return completion_path


def _build_quality_attempt_ledger(
    root: Path,
    *,
    output_dir: Path,
    run_id: str,
    case_id: str,
    variant_id: str,
    current_attempt: _QualityAttempt,
) -> tuple[dict[str, Any], QualityArtifactReference]:
    attempt_dir = output_dir / "attempts" / case_id / variant_id
    start_paths = sorted(attempt_dir.glob("*.start.json"))
    starts: list[tuple[Path, dict[str, Any], datetime]] = []
    for expected_number, start_path in enumerate(start_paths, start=1):
        payload = read_json(start_path)
        if not isinstance(payload, dict):
            raise ValueError("quality attempt start receipt is invalid")
        started_at_text = payload.get("started_at")
        if (
            payload.get("schema_version")
            != "nslab.quality_prediction_attempt_start.v1"
            or payload.get("prediction_code_version")
            != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
            or payload.get("run_id") != run_id
            or payload.get("case_id") != case_id
            or payload.get("variant_id") != variant_id
            or payload.get("attempt_number") != expected_number
            or not isinstance(payload.get("attempt_id"), str)
            or not isinstance(started_at_text, str)
        ):
            raise ValueError("quality attempt start receipt identity drifted")
        starts.append((start_path, payload, parse_datetime(started_at_text)))
    if not starts or starts[-1][1]["attempt_id"] != current_attempt.attempt_id:
        raise ValueError("quality attempt ledger does not end at the current attempt")
    attempt_rows: list[dict[str, Any]] = []
    recovery_count = 0
    for index, (start_path, start, started_at) in enumerate(starts):
        attempt_number = int(start["attempt_number"])
        completion_path = start_path.with_name(
            f"{attempt_number:04d}.completion.json"
        )
        if completion_path.is_file():
            completion = read_json(completion_path)
            if (
                not isinstance(completion, dict)
                or completion.get("schema_version")
                != "nslab.quality_prediction_attempt_completion.v1"
                or completion.get("prediction_code_version")
                != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
                or completion.get("attempt_id") != start["attempt_id"]
                or completion.get("attempt_number") != attempt_number
                or not isinstance(completion.get("elapsed_seconds"), (int, float))
                or not isinstance(completion.get("runtime_metrics"), dict)
            ):
                raise ValueError("quality attempt completion receipt is invalid")
            exact_elapsed = float(completion["elapsed_seconds"])
            if not math.isfinite(exact_elapsed) or exact_elapsed < 0.0:
                raise ValueError("quality attempt completion elapsed time is invalid")
            metrics, metric_statuses = _completed_runtime_metric_observation(
                completion["runtime_metrics"]
            )
            elapsed_status = "EXACT"
            exact_seconds: float | None = exact_elapsed
            lower_seconds = exact_elapsed
            upper_seconds = exact_elapsed
            status = "COMPLETED_RECEIPT_EXACT"
            completion_reference: dict[str, str] | None = _path_reference(
                root,
                completion_path,
            ).model_dump(mode="json")
        else:
            next_started_at = (
                starts[index + 1][2] if index + 1 < len(starts) else now_kst()
            )
            (
                lower_seconds,
                upper_seconds,
                metrics,
                metric_statuses,
            ) = _recover_interrupted_attempt_metrics(
                root,
                start=start,
                started_at=started_at,
                ended_before=next_started_at,
            )
            elapsed_status = "BOUNDED_RECOVERY"
            exact_seconds = None
            status = "RECOVERED_INTERRUPTED_BOUNDED"
            completion_reference = None
            recovery_count += 1
        attempt_rows.append(
            {
                "attempt_number": attempt_number,
                "attempt_id": start["attempt_id"],
                "status": status,
                "started_at": started_at.isoformat(),
                "elapsed_accounting_status": elapsed_status,
                "elapsed_exact_seconds": exact_seconds,
                "elapsed_lower_bound_seconds": lower_seconds,
                "elapsed_upper_bound_seconds": upper_seconds,
                "runtime_metrics": metrics,
                "runtime_metric_statuses": metric_statuses,
                "start_receipt": _path_reference(root, start_path).model_dump(
                    mode="json"
                ),
                "completion_receipt": completion_reference,
            }
        )
    cumulative_metrics, cumulative_metric_statuses = (
        _aggregate_attempt_runtime_metrics(attempt_rows)
    )
    exact_completed = sum(
        float(row["elapsed_exact_seconds"] or 0.0) for row in attempt_rows
    )
    lower_bound = sum(
        float(row["elapsed_lower_bound_seconds"]) for row in attempt_rows
    )
    upper_bound = sum(
        float(row["elapsed_upper_bound_seconds"]) for row in attempt_rows
    )
    contains_recovered = recovery_count > 0
    unavailable_metrics = any(
        status in {"UNAVAILABLE", "PARTIAL_LOWER_BOUND"}
        for status in cumulative_metric_statuses.values()
    )
    ledger = {
        "schema_version": QUALITY_ATTEMPT_LEDGER_VERSION,
        "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
        "run_id": run_id,
        "case_id": case_id,
        "variant_id": variant_id,
        "attempt_count": len(attempt_rows),
        "recovered_interrupted_attempt_count": recovery_count,
        "contains_recovered_attempts": contains_recovered,
        "elapsed_accounting_status": (
            "BOUNDED_RECOVERY" if contains_recovered else "EXACT"
        ),
        "elapsed_exact_completed_seconds": exact_completed,
        "elapsed_lower_bound_seconds": lower_bound,
        "elapsed_upper_bound_seconds": upper_bound,
        "runtime_metrics": cumulative_metrics,
        "runtime_metric_statuses": cumulative_metric_statuses,
        "runtime_metrics_accounting_status": (
            "RECOVERED_PARTIAL"
            if contains_recovered and unavailable_metrics
            else "RECOVERED_LOWER_BOUND"
            if contains_recovered
            else "PARTIAL_UNAVAILABLE"
            if unavailable_metrics
            else "EXACT"
        ),
        "attempts": attempt_rows,
    }
    ledger_path = current_attempt.start_path.with_name(
        f"{current_attempt.attempt_number:04d}.ledger.json"
    )
    _write_immutable_bytes(ledger_path, _pretty_json_bytes(ledger))
    return ledger, _path_reference(root, ledger_path)


def _recover_interrupted_attempt_metrics(
    root: Path,
    *,
    start: dict[str, Any],
    started_at: datetime,
    ended_before: datetime,
) -> tuple[float, float, dict[str, int | float | None], dict[str, str]]:
    runtime_before = start.get("runtime_before")
    if not isinstance(runtime_before, dict) or not isinstance(
        runtime_before.get("trace_files"),
        list,
    ):
        raise ValueError("quality attempt trace baseline is invalid")
    baseline = {str(value) for value in runtime_before["trace_files"]}
    trace_dir = root / "runs" / "traces"
    paths = sorted(
        path.resolve()
        for path in trace_dir.glob("*.json")
        if path.is_file() and path.resolve().as_posix() not in baseline
    )
    logical = live = checkpoint = prompt = completion = retries = errors = 0
    observed_trace_count = 0
    last_observed = started_at
    for path in paths:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        trace_started_text = payload.get("started_at")
        if not isinstance(trace_started_text, str):
            continue
        trace_started = parse_datetime(trace_started_text)
        if trace_started < started_at or trace_started >= ended_before:
            continue
        observed_trace_count += 1
        operation = str(payload.get("operation") or "")
        status = str(payload.get("status") or "")
        is_logical = operation in {"generate_text", "generate_structured"}
        logical += int(is_logical)
        checkpoint += int(is_logical and status == "checkpoint_hit")
        retry_count = int(payload.get("retries") or 0)
        retries += retry_count
        live += int(is_logical and status != "checkpoint_hit") + retry_count
        errors += int(status == "error")
        token_usage = payload.get("token_usage")
        if isinstance(token_usage, dict):
            prompt += int(token_usage.get("prompt_tokens_estimate") or 0)
            completion += int(token_usage.get("completion_tokens_estimate") or 0)
        ended_text = payload.get("completed_at") or payload.get("ended_at")
        observed_end = (
            parse_datetime(ended_text)
            if isinstance(ended_text, str)
            else trace_started
        )
        if observed_end < trace_started or observed_end > ended_before:
            raise ValueError("quality recovered trace time escapes attempt bounds")
        last_observed = max(last_observed, observed_end)
    lower_bound = max(0.0, (last_observed - started_at).total_seconds())
    upper_bound = max(0.0, (ended_before - started_at).total_seconds())
    if lower_bound > upper_bound:
        raise ValueError("quality recovered elapsed bounds are invalid")
    observed_metrics: dict[str, int | float | None] = {
        "logical_llm_call_count": logical,
        "oauth_live_agent_call_count": live,
        "llm_checkpoint_hit_count": checkpoint,
        "oauth_cache_event_count": checkpoint,
        "llm_prompt_tokens_estimate": prompt,
        "llm_completion_tokens_estimate": completion,
        "llm_retry_count": retries,
        "llm_error_trace_count": errors,
        "new_llm_trace_count": observed_trace_count,
    }
    metrics = {
        key: observed_metrics.get(key) for key in QUALITY_RUNTIME_COUNTER_KEYS
    }
    statuses = {
        key: "LOWER_BOUND" if key in observed_metrics else "UNAVAILABLE"
        for key in QUALITY_RUNTIME_COUNTER_KEYS
    }
    return lower_bound, upper_bound, metrics, statuses


def _completed_runtime_metric_observation(
    runtime_metrics: dict[str, Any],
) -> tuple[dict[str, int | float | None], dict[str, str]]:
    metrics: dict[str, int | float | None] = {}
    statuses: dict[str, str] = {}
    for key in QUALITY_RUNTIME_COUNTER_KEYS:
        value = runtime_metrics.get(key)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) >= 0.0
        ):
            metrics[key] = value
            statuses[key] = "EXACT"
        else:
            metrics[key] = None
            statuses[key] = "UNAVAILABLE"
    return metrics, statuses


def _aggregate_attempt_runtime_metrics(
    attempt_rows: list[dict[str, Any]],
) -> tuple[dict[str, int | float | None], dict[str, str]]:
    metrics: dict[str, int | float | None] = {}
    statuses: dict[str, str] = {}
    for key in QUALITY_RUNTIME_COUNTER_KEYS:
        observations = [
            (
                row["runtime_metrics"].get(key),
                row["runtime_metric_statuses"].get(key, "UNAVAILABLE"),
            )
            for row in attempt_rows
        ]
        numeric = [
            value
            for value, _status in observations
            if isinstance(value, int | float) and not isinstance(value, bool)
        ]
        if not numeric:
            metrics[key] = None
            statuses[key] = "UNAVAILABLE"
            continue
        metrics[key] = (
            max(numeric) if key in QUALITY_RUNTIME_GAUGE_KEYS else sum(numeric)
        )
        observed_statuses = {status for _value, status in observations}
        if observed_statuses == {"EXACT"}:
            statuses[key] = "EXACT"
        elif "UNAVAILABLE" in observed_statuses or "PARTIAL_LOWER_BOUND" in (
            observed_statuses
        ):
            statuses[key] = "PARTIAL_LOWER_BOUND"
        else:
            statuses[key] = "LOWER_BOUND"
    return dict(sorted(metrics.items())), dict(sorted(statuses.items()))


def _build_shared_preparation_ledger(
    root: Path,
    *,
    run_id: str,
    case_id: str,
    current_attempt: _SharedPreparationAttempt,
    shared_context_sha256: str,
    shared_manifest_sha256: str,
) -> tuple[dict[str, Any], QualityArtifactReference]:
    attempt_dir = current_attempt.start_path.parent
    start_paths = sorted(attempt_dir.glob("*.start.json"))
    starts: list[tuple[Path, dict[str, Any], datetime]] = []
    for expected_number, start_path in enumerate(start_paths, start=1):
        payload = read_json(start_path)
        started_at_text = payload.get("started_at") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version")
            != "nslab.quality_shared_preparation_start.v1"
            or payload.get("prediction_code_version")
            != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
            or payload.get("scope_id") != current_attempt.scope_id
            or payload.get("case_id") != case_id
            or payload.get("attempt_number") != expected_number
            or not isinstance(payload.get("attempt_id"), str)
            or not isinstance(started_at_text, str)
        ):
            raise ValueError("quality shared preparation start receipt is invalid")
        starts.append((start_path, payload, parse_datetime(started_at_text)))
    if not starts or starts[-1][1]["attempt_id"] != current_attempt.attempt_id:
        raise ValueError("quality shared preparation ledger is not current")
    rows: list[dict[str, Any]] = []
    build_count = cache_count = recovery_count = 0
    for index, (start_path, start, started_at) in enumerate(starts):
        attempt_number = int(start["attempt_number"])
        completion_path = start_path.with_name(
            f"{attempt_number:04d}.completion.json"
        )
        if completion_path.is_file():
            completion = read_json(completion_path)
            if (
                not isinstance(completion, dict)
                or completion.get("schema_version")
                != "nslab.quality_shared_preparation_completion.v1"
                or completion.get("prediction_code_version")
                != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
                or completion.get("attempt_id") != start["attempt_id"]
                or completion.get("attempt_number") != attempt_number
                or completion.get("shared_context_sha256")
                != shared_context_sha256
                or completion.get("shared_manifest_sha256")
                != shared_manifest_sha256
                or not isinstance(completion.get("cache_hit"), bool)
                or not isinstance(completion.get("elapsed_seconds"), int | float)
                or not isinstance(completion.get("runtime_metrics"), dict)
            ):
                raise ValueError(
                    "quality shared preparation completion receipt is invalid"
                )
            exact_seconds = float(completion["elapsed_seconds"])
            if not math.isfinite(exact_seconds) or exact_seconds < 0.0:
                raise ValueError("quality shared preparation elapsed time is invalid")
            metrics, metric_statuses = _completed_runtime_metric_observation(
                completion["runtime_metrics"]
            )
            cache_hit = bool(completion["cache_hit"])
            cache_count += int(cache_hit)
            build_count += int(not cache_hit)
            row = {
                "attempt_number": attempt_number,
                "attempt_id": start["attempt_id"],
                "status": (
                    "CACHE_LOAD_COMPLETED_EXACT"
                    if cache_hit
                    else "BUILD_COMPLETED_EXACT"
                ),
                "started_at": started_at.isoformat(),
                "elapsed_accounting_status": "EXACT",
                "elapsed_exact_seconds": exact_seconds,
                "elapsed_lower_bound_seconds": exact_seconds,
                "elapsed_upper_bound_seconds": exact_seconds,
                "runtime_metrics": metrics,
                "runtime_metric_statuses": metric_statuses,
                "start_receipt": _path_reference(root, start_path).model_dump(
                    mode="json"
                ),
                "completion_receipt": _path_reference(
                    root, completion_path
                ).model_dump(mode="json"),
            }
        else:
            if index + 1 >= len(starts):
                raise ValueError(
                    "current shared preparation attempt has no completion receipt"
                )
            next_started_at = starts[index + 1][2]
            (
                lower_seconds,
                upper_seconds,
                metrics,
                metric_statuses,
            ) = _recover_interrupted_attempt_metrics(
                root,
                start=start,
                started_at=started_at,
                ended_before=next_started_at,
            )
            recovery_count += 1
            row = {
                "attempt_number": attempt_number,
                "attempt_id": start["attempt_id"],
                "status": "RECOVERED_INTERRUPTED_BOUNDED",
                "started_at": started_at.isoformat(),
                "elapsed_accounting_status": "BOUNDED_RECOVERY",
                "elapsed_exact_seconds": None,
                "elapsed_lower_bound_seconds": lower_seconds,
                "elapsed_upper_bound_seconds": upper_seconds,
                "runtime_metrics": metrics,
                "runtime_metric_statuses": metric_statuses,
                "start_receipt": _path_reference(root, start_path).model_dump(
                    mode="json"
                ),
                "completion_receipt": None,
            }
        rows.append(row)
    metrics, metric_statuses = _aggregate_attempt_runtime_metrics(rows)
    contains_recovered = recovery_count > 0
    unavailable = any(
        status in {"UNAVAILABLE", "PARTIAL_LOWER_BOUND"}
        for status in metric_statuses.values()
    )
    ledger = {
        "schema_version": "nslab.quality_shared_preparation_ledger.v1",
        "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
        "run_id": run_id,
        "scope_id": current_attempt.scope_id,
        "case_id": case_id,
        "shared_context_sha256": shared_context_sha256,
        "shared_manifest_sha256": shared_manifest_sha256,
        "attempt_count": len(rows),
        "build_attempt_count": build_count,
        "cache_load_attempt_count": cache_count,
        "recovered_interrupted_attempt_count": recovery_count,
        "contains_recovered_attempts": contains_recovered,
        "elapsed_accounting_status": (
            "BOUNDED_RECOVERY" if contains_recovered else "EXACT"
        ),
        "elapsed_exact_completed_seconds": sum(
            float(row["elapsed_exact_seconds"] or 0.0) for row in rows
        ),
        "elapsed_lower_bound_seconds": sum(
            float(row["elapsed_lower_bound_seconds"]) for row in rows
        ),
        "elapsed_upper_bound_seconds": sum(
            float(row["elapsed_upper_bound_seconds"]) for row in rows
        ),
        "runtime_metrics": metrics,
        "runtime_metric_statuses": metric_statuses,
        "runtime_metrics_accounting_status": (
            "RECOVERED_PARTIAL"
            if contains_recovered and unavailable
            else "RECOVERED_LOWER_BOUND"
            if contains_recovered
            else "PARTIAL_UNAVAILABLE"
            if unavailable
            else "EXACT"
        ),
        "attempts": rows,
    }
    ledger_path = current_attempt.start_path.with_name(
        f"{current_attempt.attempt_number:04d}.ledger.json"
    )
    _write_immutable_bytes(ledger_path, _pretty_json_bytes(ledger))
    return ledger, _path_reference(root, ledger_path)


@dataclass(frozen=True)
class QualityRuntimeScoreResult:
    report: dict[str, Any]
    report_path: Path
    markdown_path: Path


async def predict_runtime_variants(
    root: Path,
    *,
    settings: Settings,
    blind_selection_path: Path,
    profile: QualityEvaluationProfile,
) -> QualityRuntimePredictionResult:
    """Seal V0/V1 predictions without reading an outcome manifest or path."""

    root = root.resolve()
    blind_selection_path = _resolve_blind_selection_path(
        root,
        blind_selection_path,
    )
    if profile.profile != "QUALITY_FULL":
        raise ValueError("quality runtime prediction requires QUALITY_FULL")
    if (
        profile.provider != settings.llm_provider
        or profile.model != settings.llm.model
        or profile.reasoning_effort
        != str(settings.llm.reasoning_effort or "")
    ):
        raise ValueError("quality runtime profile differs from runtime settings")
    selection = load_blind_runtime_selection(root, blind_selection_path)
    blind_selection_reference = _path_reference(root, blind_selection_path)
    current_brain_path = root / "brain" / "current" / "brain_manifest.json"
    current_coverage_path = root / "brain" / "current" / "coverage_manifest.json"
    brain = read_json(current_brain_path)
    coverage = read_json(current_coverage_path)
    if not isinstance(brain, dict) or brain.get("build_mode") != "llm-full":
        raise ValueError("quality runtime prediction requires an llm-full brain")
    if (
        not isinstance(coverage, dict)
        or coverage.get("coverage_scope") != "EVALUATION_REPLAY_BUILD"
    ):
        raise ValueError("quality runtime prediction requires BUILD-only coverage")
    brain_version = brain.get("brain_version")
    if not isinstance(brain_version, str) or not brain_version.strip():
        raise ValueError("quality runtime brain has no immutable version")
    brain_path = root / "brain" / "snapshots" / brain_version / "brain_manifest.json"
    coverage_path = root / "brain" / "snapshots" / brain_version / "coverage_manifest.json"
    if (
        not brain_path.is_file()
        or not coverage_path.is_file()
        or file_sha256(brain_path) != file_sha256(current_brain_path)
        or file_sha256(coverage_path) != file_sha256(current_coverage_path)
    ):
        raise ValueError("quality runtime current brain is not pinned to its snapshot")
    brain_reference = _path_reference(root, brain_path)
    coverage_reference = _path_reference(root, coverage_path)
    snapshot_id = brain.get("production_memory_snapshot_id")
    active_snapshot = active_memory_snapshot_manifest(root)
    if (
        not isinstance(snapshot_id, str)
        or active_snapshot is None
        or active_snapshot.snapshot_id != snapshot_id
        or not active_snapshot.evaluation_only
    ):
        raise ValueError(
            "quality runtime prediction requires the evaluation-only BUILD snapshot"
        )
    base_llm = create_llm_provider(settings)
    embedding_provider = create_configured_embedding_provider(
        settings,
        production=True,
        llm_provider=base_llm,
    )
    retrieval = LocalRetrievalStore(root, force_empty=True)
    analyzers = {
        "V0": DailyAnalyzer(
            settings,
            llm=base_llm,
            retrieval=retrieval,
            embedding_provider=embedding_provider,
            runtime_retrieval_variant="legacy",
            configure_price_source=False,
        ),
        "V1": DailyAnalyzer(
            settings,
            llm=base_llm,
            retrieval=retrieval,
            embedding_provider=embedding_provider,
            runtime_retrieval_variant="v4",
            configure_price_source=False,
        ),
    }
    prepared_cases: list[_PreparedPredictionCase] = []
    preflight_dir = (
        root
        / "runs"
        / "semantic_brain_upgrade"
        / "quality_full"
        / "prediction_inputs"
        / selection.selection_id
    )
    for case in selection.cases:
        news_input = materialize_blind_case_news(
            root,
            case=case,
            output_dir=preflight_dir,
        )
        cutoff_at = parse_datetime(news_input.cutoff_at)
        shared_scope_id = _shared_preparation_scope_id(
            blind_selection=blind_selection_reference,
            case=case,
            profile=profile,
        )
        shared_runtime_before = _runtime_counter_snapshot(
            root,
            llm=base_llm,
            embedding_provider=embedding_provider,
        )
        shared_attempt = _begin_shared_preparation_attempt(
            root,
            scope_id=shared_scope_id,
            case_id=case.episode_id,
            runtime_before=shared_runtime_before,
        )
        shared_started = time.perf_counter()
        shared = await build_shared_pre_retrieval_context(
            root,
            settings=settings,
            profile=profile,
            news_csv=news_input.news_csv_path,
            trade_date=case.trade_date,
            cutoff_at=cutoff_at,
            d_minus_one_context=news_input.d_minus_one_context,
            d_minus_one_reference=(
                news_input.d_minus_one_context_reference
            ),
            trusted_cache_context_sha256=(
                _trusted_shared_context_anchor_from_prediction_seals(
                    root,
                    blind_selection=blind_selection_reference,
                    case=case,
                    profile=profile,
                )
            ),
            analyzer=analyzers["V0"],
        )
        shared_elapsed_seconds = time.perf_counter() - shared_started
        shared_runtime_metrics = _runtime_counter_delta(
            root,
            before=shared_runtime_before,
            llm=base_llm,
            embedding_provider=embedding_provider,
        )
        if (
            shared.news_batch.sha256 != news_input.news_sha256
            or shared.context.news_sha256 != news_input.news_sha256
            or shared.news_batch.path.resolve() != news_input.news_csv_path.resolve()
        ):
            raise ValueError("shared current-news identity drifted after materialization")
        shared_sha256 = shared.manifest.context.sha256
        shared_manifest_sha256 = file_sha256(shared.manifest_path)
        if file_sha256(shared.context_path) != shared_sha256:
            raise ValueError("shared context changed after its verified build")
        d_minus_one_reference = news_input.d_minus_one_context_reference
        d_minus_one_path = news_input.d_minus_one_context_path
        d_minus_one_context = news_input.d_minus_one_context
        if (
            shared.context.d_minus_one_safe_context != d_minus_one_reference
            or d_minus_one_reference.sha256 != case.d_minus_one_context_sha256
            or d_minus_one_context.trade_date != case.trade_date
            or as_kst(d_minus_one_context.cutoff_at) != as_kst(case.cutoff_at)
            or d_minus_one_context.allowed_through
            != case.trade_date - timedelta(days=1)
            or d_minus_one_context.source_revision_sha256
            != case.d_minus_one_source_revision_sha256
            or d_minus_one_context.snapshot_session_date
            != case.d_minus_one_snapshot_session_date
        ):
            raise ValueError("shared D-minus-one temporal identity drifted")
        d_minus_one_payload_sha256 = news_input.d_minus_one_payload_sha256
        _complete_shared_preparation_attempt(
            shared_attempt,
            elapsed_seconds=shared_elapsed_seconds,
            runtime_metrics=shared_runtime_metrics,
            cache_hit=shared.cache_hit,
            shared_context_sha256=shared_sha256,
            shared_manifest_sha256=shared_manifest_sha256,
        )
        prepared_cases.append(
            _PreparedPredictionCase(
                case=case,
                news_input=news_input,
                cutoff_at=cutoff_at,
                shared=shared,
                shared_context_sha256=shared_sha256,
                shared_manifest_sha256=shared_manifest_sha256,
                shared_preparation_attempt=shared_attempt,
                d_minus_one_reference=d_minus_one_reference,
                d_minus_one_path=d_minus_one_path,
                d_minus_one_context=d_minus_one_context,
                d_minus_one_payload_sha256=d_minus_one_payload_sha256,
            )
        )
    case_contexts = [
        {
            "case_id": prepared.case.episode_id,
            "shared_preparation_scope_id": (
                prepared.shared_preparation_attempt.scope_id
            ),
            "news_sha256": prepared.news_input.news_sha256,
            "parsed_news_root_sha256": (
                prepared.shared.context.parsed_news_root_sha256
            ),
            "shared_context_sha256": prepared.shared_context_sha256,
            "shared_manifest_sha256": prepared.shared_manifest_sha256,
            "d_minus_one_context_sha256": (
                prepared.d_minus_one_reference.sha256
            ),
            "d_minus_one_payload_sha256": (
                prepared.d_minus_one_payload_sha256
            ),
            "d_minus_one_candidate_universe_root_sha256": (
                prepared.d_minus_one_context.candidate_universe_root_sha256
            ),
            "d_minus_one_snapshot_root_sha256": (
                prepared.d_minus_one_context.snapshot_root_sha256
            ),
            "d_minus_one_source_revision_sha256": (
                prepared.d_minus_one_context.source_revision_sha256
            ),
            "d_minus_one_snapshot_session_date": (
                prepared.d_minus_one_context.snapshot_session_date.isoformat()
                if prepared.d_minus_one_context.snapshot_session_date is not None
                else None
            ),
        }
        for prepared in prepared_cases
    ]
    case_context_root_sha256 = sha256_text(canonical_json(case_contexts))
    variant_architectures: dict[str, str] = {
        variant_id: _variant_architecture_sha256(
            variant_id=variant_id,
            profile=profile,
            brain_manifest=brain_reference,
            coverage_manifest=coverage_reference,
            memory_snapshot_id=snapshot_id,
        )
        for variant_id in QUALITY_RUNTIME_VARIANTS
    }
    identity = {
        "schema_version": "nslab.quality_runtime_prediction.v4",
        "prediction_code_version": QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
        "d_minus_one_parity_version": "SEALED_SHARED_D_MINUS_ONE_CONTEXT.v4",
        "prediction_input_boundary_version": PREDICTION_INPUT_BOUNDARY_VERSION,
        "blind_selection_sha256": file_sha256(blind_selection_path),
        "profile": profile.model_dump(mode="json"),
        "brain_manifest_sha256": brain_reference.sha256,
        "coverage_manifest_sha256": coverage_reference.sha256,
        "memory_snapshot_id": snapshot_id,
        "case_context_root_sha256": case_context_root_sha256,
        "case_contexts": case_contexts,
        "variants": list(QUALITY_RUNTIME_VARIANTS),
        "variant_architectures": variant_architectures,
        "outcome_reference_count": 0,
    }
    run_id = stable_id(
        "QPRED",
        canonical_json(identity),
        length=20,
    )
    output_dir = (
        root
        / "runs"
        / "semantic_brain_upgrade"
        / "quality_full"
        / "predictions"
        / run_id
    )
    shared_preparation_ledgers = {
        prepared.case.episode_id: _build_shared_preparation_ledger(
            root,
            run_id=run_id,
            case_id=prepared.case.episode_id,
            current_attempt=prepared.shared_preparation_attempt,
            shared_context_sha256=prepared.shared_context_sha256,
            shared_manifest_sha256=prepared.shared_manifest_sha256,
        )[1]
        for prepared in prepared_cases
    }
    manifest_path = output_dir / "paired_prediction_manifest.json"
    manifest = _load_or_create_prediction_manifest(
        root=root,
        path=manifest_path,
        run_id=run_id,
        profile=profile,
        blind_selection_path=blind_selection_path,
        expected_case_ids=[case.episode_id for case in selection.cases],
        expected_variant_architecture_sha256=variant_architectures,
        shared_preparation_ledgers=shared_preparation_ledgers,
    )
    if tuple(manifest.expected_variant_ids) != QUALITY_RUNTIME_VARIANTS:
        raise ValueError("quality runtime prediction variant set is unsupported")
    _write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
    completed = {
        (seal.case_id, seal.variant_id): seal for seal in manifest.seals
    }
    for prepared in prepared_cases:
        case = prepared.case
        news_input = prepared.news_input
        cutoff_at = prepared.cutoff_at
        shared = prepared.shared
        shared_sha256 = prepared.shared_context_sha256
        shared_manifest_sha256 = prepared.shared_manifest_sha256
        d_minus_one_reference = prepared.d_minus_one_reference
        d_minus_one_path = prepared.d_minus_one_path
        d_minus_one_context = prepared.d_minus_one_context
        d_minus_one_payload_sha256 = prepared.d_minus_one_payload_sha256
        candidate_universe_policy_sha256 = _candidate_universe_policy_sha256(
            settings=settings,
            profile=profile,
            case=case,
            parsed_news_root_sha256=shared.context.parsed_news_root_sha256,
            shared_context_sha256=shared_sha256,
            brain_manifest=brain_reference,
            coverage_manifest=coverage_reference,
            memory_snapshot_id=snapshot_id,
            d_minus_one_context=d_minus_one_reference,
            d_minus_one_candidate_universe_root_sha256=(
                d_minus_one_context.candidate_universe_root_sha256
            ),
            d_minus_one_snapshot_root_sha256=(
                d_minus_one_context.snapshot_root_sha256
            ),
            d_minus_one_source_revision_sha256=(
                d_minus_one_context.source_revision_sha256
            ),
            d_minus_one_snapshot_session_date=(
                d_minus_one_context.snapshot_session_date
            ),
            d_minus_one_payload_sha256=d_minus_one_payload_sha256,
        )
        for raw_variant_id in manifest.expected_variant_ids:
            if raw_variant_id not in QUALITY_RUNTIME_VARIANTS:
                raise ValueError("quality runtime prediction variant is unsupported")
            variant_id = raw_variant_id
            seal_path = (
                output_dir
                / "seals"
                / case.episode_id
                / f"{variant_id}.json"
            )
            completed_seal, recovered_orphan = _reconcile_immutable_seal_state(
                manifest,
                seal_path=seal_path,
                case_id=case.episode_id,
                variant_id=variant_id,
            )
            if completed_seal is not None:
                _verify_completed_prediction_seal_identity(
                    root=root,
                    seal=completed_seal,
                    case=case,
                    shared_context_sha256=shared_sha256,
                    parsed_news_root_sha256=(
                        shared.context.parsed_news_root_sha256
                    ),
                    d_minus_one_reference=d_minus_one_reference,
                    d_minus_one_context=d_minus_one_context,
                    d_minus_one_payload_sha256=d_minus_one_payload_sha256,
                    candidate_universe_policy_sha256=(
                        candidate_universe_policy_sha256
                    ),
                    brain_manifest=brain_reference,
                    coverage_manifest=coverage_reference,
                    memory_snapshot_id=snapshot_id,
                    prediction_run_id=run_id,
                    variant_architecture_sha256=(
                        variant_architectures[variant_id]
                    ),
                )
                if recovered_orphan:
                    manifest = _prediction_manifest_with_seal(
                        manifest,
                        seal=completed_seal,
                    )
                    _write_json_atomic(
                        manifest_path,
                        manifest.model_dump(mode="json"),
                    )
                    completed[(case.episode_id, variant_id)] = completed_seal
                continue
            runtime_before = _runtime_counter_snapshot(
                root,
                llm=base_llm,
                embedding_provider=embedding_provider,
            )
            attempt = _begin_quality_attempt(
                root,
                output_dir=output_dir,
                run_id=run_id,
                case_id=case.episode_id,
                variant_id=variant_id,
                runtime_before=runtime_before,
            )
            started = time.perf_counter()
            analysis = await analyzers[variant_id].analyze(
                news_csv=news_input.news_csv_path,
                trade_date=case.trade_date,
                cutoff_at=cutoff_at,
                mode="exhaustive",
                web_search=False,
                shared_pre_retrieval_context_path=shared.context_path,
                shared_pre_retrieval_context_sha256=shared_sha256,
                shared_pre_retrieval_manifest_sha256=(
                    shared_manifest_sha256
                ),
                sealed_blind_input_manifest_sha256=(
                    case.blind_input_manifest.sha256
                ),
                preloaded_news_batch=shared.news_batch,
                shared_d_minus_one_context_path=d_minus_one_path,
            )
            elapsed_seconds = time.perf_counter() - started
            runtime_metrics = _runtime_counter_delta(
                root,
                before=runtime_before,
                llm=base_llm,
                embedding_provider=embedding_provider,
            )
            context_manifest = analysis.context_manifest
            if context_manifest.errors:
                raise ValueError(
                    "QUALITY_FULL context manifest contains errors: "
                    + canonical_json(context_manifest.errors)
                )
            if (
                context_manifest.shared_pre_retrieval_context_sha256
                != shared_sha256
            ):
                raise ValueError(
                    "quality runtime variant did not consume the sealed shared context"
                )
            if (
                context_manifest.prediction_input_boundary_version
                != PREDICTION_INPUT_BOUNDARY_VERSION
                or context_manifest.sealed_blind_input_manifest_sha256
                != case.blind_input_manifest.sha256
            ):
                raise ValueError(
                    "quality runtime analyzer did not bind the sealed blind input"
                )
            if (
                context_manifest.news_sha256 != case.news_sha256
                or context_manifest.parsed_news_root_sha256
                != shared.context.parsed_news_root_sha256
                or as_kst(context_manifest.cutoff_at) != as_kst(case.cutoff_at)
                or context_manifest.brain_version != brain_version
            ):
                raise ValueError("quality runtime analyzer identity drifted")
            if (
                context_manifest.d_minus_one_context_artifact
                != d_minus_one_reference.artifact_path
                or context_manifest.d_minus_one_context_sha256
                != d_minus_one_reference.sha256
                or context_manifest.d_minus_one_candidate_universe_root_sha256
                != d_minus_one_context.candidate_universe_root_sha256
                or context_manifest.d_minus_one_snapshot_root_sha256
                != d_minus_one_context.snapshot_root_sha256
                or context_manifest.d_minus_one_source_revision_sha256
                != d_minus_one_context.source_revision_sha256
                or context_manifest.d_minus_one_snapshot_session_date
                != d_minus_one_context.snapshot_session_date
                    or context_manifest.d_minus_one_payload_sha256
                    != d_minus_one_payload_sha256
                    or context_manifest.d_minus_one_projection_status
                    != "BOUND"
                ):
                raise ValueError(
                    "quality runtime analyzer did not bind the shared D-minus-one context"
                )
            trace_stats = _trace_stats(
                root,
                context_manifest,
                expected_prediction_id=analysis.blind_prediction.prediction_id,
                require_complete_provenance=True,
            )
            if trace_stats["memory_snapshot_id"] != snapshot_id:
                raise ValueError("quality runtime variant used the wrong snapshot")
            if context_manifest.blind_web_search_call_count:
                raise ValueError("quality runtime variant attempted BLIND web access")
            if int(trace_stats["future_record_count"]):
                raise ValueError("quality runtime variant exposed future records")
            if int(trace_stats["online_full_scan_count"]):
                raise ValueError("quality runtime variant performed an online full scan")
            prediction_artifact = context_manifest.prediction_artifact
            if not prediction_artifact:
                raise ValueError("quality runtime prediction artifact is missing")
            context_manifest_path = (
                root
                / "runs"
                / "manifests"
                / f"{context_manifest.run_id}.json"
            )
            consumed_d_minus_one_projection = (
                _verify_final_synthesis_d_minus_one_consumption(
                    root=root,
                    context=context_manifest,
                    expected_context=d_minus_one_context,
                )
            )
            _complete_quality_attempt(
                attempt,
                elapsed_seconds=elapsed_seconds,
                runtime_metrics=runtime_metrics,
            )
            attempt_ledger, attempt_ledger_reference = (
                _build_quality_attempt_ledger(
                    root,
                    output_dir=output_dir,
                    run_id=run_id,
                    case_id=case.episode_id,
                    variant_id=variant_id,
                    current_attempt=attempt,
                )
            )
            efficiency = {
                "elapsed_accounting_status": attempt_ledger[
                    "elapsed_accounting_status"
                ],
                "elapsed_exact_completed_seconds": attempt_ledger[
                    "elapsed_exact_completed_seconds"
                ],
                "elapsed_lower_bound_seconds": attempt_ledger[
                    "elapsed_lower_bound_seconds"
                ],
                "elapsed_upper_bound_seconds": attempt_ledger[
                    "elapsed_upper_bound_seconds"
                ],
                "contains_recovered_attempts": attempt_ledger[
                    "contains_recovered_attempts"
                ],
                "runtime_metrics": attempt_ledger["runtime_metrics"],
                "runtime_metric_statuses": attempt_ledger[
                    "runtime_metric_statuses"
                ],
                "runtime_metrics_accounting_status": attempt_ledger[
                    "runtime_metrics_accounting_status"
                ],
                "attempt_count": attempt_ledger["attempt_count"],
                "recovered_interrupted_attempt_count": attempt_ledger[
                    "recovered_interrupted_attempt_count"
                ],
                "attempt_ledger_artifact_path": (
                    attempt_ledger_reference.artifact_path
                ),
                "attempt_ledger_sha256": attempt_ledger_reference.sha256,
            }
            seal = PredictionSeal(
                case_id=case.episode_id,
                variant_id=variant_id,
                variant_architecture_sha256=variant_architectures[variant_id],
                sealed_at=now_kst(),
                cutoff_at=case.cutoff_at,
                blind_input_manifest=case.blind_input_manifest,
                news_sha256=news_input.news_sha256,
                parsed_news_root_sha256=(
                    shared.context.parsed_news_root_sha256
                ),
                shared_context_sha256=shared_sha256,
                brain_manifest=brain_reference,
                coverage_manifest=coverage_reference,
                memory_snapshot_id=snapshot_id,
                d_minus_one_context=d_minus_one_reference,
                d_minus_one_context_sha256=d_minus_one_reference.sha256,
                d_minus_one_candidate_universe_root_sha256=(
                    d_minus_one_context.candidate_universe_root_sha256
                ),
                d_minus_one_snapshot_root_sha256=(
                    d_minus_one_context.snapshot_root_sha256
                ),
                d_minus_one_source_revision_sha256=(
                    d_minus_one_context.source_revision_sha256
                ),
                d_minus_one_snapshot_session_date=(
                    d_minus_one_context.snapshot_session_date
                ),
                d_minus_one_payload_sha256=d_minus_one_payload_sha256,
                d_minus_one_consumed_payload_sha256=(
                    sha256_text(
                        canonical_json(
                            consumed_d_minus_one_projection.model_dump(
                                mode="json"
                            )
                        )
                    )
                ),
                d_minus_one_projection_policy=(
                    consumed_d_minus_one_projection.projection_policy
                ),
                d_minus_one_projection_root_sha256=(
                    consumed_d_minus_one_projection.projection_root_sha256
                ),
                d_minus_one_projection_requested_ticker_count=len(
                    consumed_d_minus_one_projection.requested_tickers
                ),
                d_minus_one_projection_snapshot_count=len(
                    consumed_d_minus_one_projection.snapshots
                ),
                d_minus_one_projection_missing_ticker_count=len(
                    consumed_d_minus_one_projection.missing_tickers
                ),
                candidate_universe_policy_sha256=(
                    candidate_universe_policy_sha256
                ),
                prediction=_path_reference(root, root / prediction_artifact),
                context_manifest=_path_reference(root, context_manifest_path),
                final_citation_count=int(
                    trace_stats["final_cited_record_count"]
                ),
                efficiency=efficiency,
            )
            _write_immutable_bytes(
                seal_path,
                _pretty_json_bytes(seal.model_dump(mode="json")),
            )
            manifest = _prediction_manifest_with_seal(manifest, seal=seal)
            _write_json_atomic(manifest_path, manifest.model_dump(mode="json"))
            completed[(case.episode_id, variant_id)] = seal
    return QualityRuntimePredictionResult(
        manifest=manifest,
        manifest_path=manifest_path,
    )


def score_runtime_variants(
    root: Path,
    *,
    paired_prediction_manifest_path: Path,
    outcome_selection_path: Path,
) -> QualityRuntimeScoreResult:
    """Open outcomes only after every expected V0/V1 prediction is sealed."""

    root = root.resolve()
    paired_prediction_manifest_path = paired_prediction_manifest_path.resolve()
    paired = PairedPredictionManifest.model_validate(
        read_json(paired_prediction_manifest_path)
    )
    if tuple(paired.expected_variant_ids) != QUALITY_RUNTIME_VARIANTS:
        raise ValueError("runtime scoring variant set is unsupported")
    if not paired.all_predictions_sealed:
        raise ValueError(
            "runtime scoring requires every expected prediction seal before outcomes"
        )
    if set(paired.shared_preparation_ledgers) != set(paired.expected_case_ids):
        raise ValueError(
            "runtime scoring requires every shared preparation ledger before outcomes"
        )
    shared_stage_by_case: dict[str, SharedStageObservation] = {}
    for case_id in paired.expected_case_ids:
        context_hashes = {
            seal.shared_context_sha256
            for seal in paired.seals
            if seal.case_id == case_id
        }
        if len(context_hashes) != 1:
            raise ValueError("runtime shared preparation context parity drifted")
        shared_stage_by_case[case_id] = (
            _validated_shared_preparation_observation(
                root,
                reference=paired.shared_preparation_ledgers[case_id],
                expected_prediction_run_id=paired.run_id,
                expected_case_id=case_id,
                expected_shared_context_sha256=next(iter(context_hashes)),
            )
        )
    paired_sha256 = file_sha256(paired_prediction_manifest_path)
    blind_selection_path = _resolve_reference(root, paired.blind_selection)
    blind_selection = _validate_blind_runtime_selection_payload(
        _resolve_blind_selection_path(root, blind_selection_path),
        _read_verified_json_reference(
            paired.blind_selection,
            blind_selection_path,
        ),
    )
    predictions = _verify_paired_prediction_closure(
        root,
        paired=paired,
        blind_selection=blind_selection,
    )
    # This is the first operation allowed to open the outcome-side manifest.
    outcome_selection = load_runtime_outcome_selection(
        outcome_selection_path.resolve()
    )
    if outcome_selection.selection_id != blind_selection.selection_id:
        raise ValueError("runtime outcome selection identity differs from predictions")
    if outcome_selection.blind_selection_sha256 != paired.blind_selection.sha256:
        raise ValueError("runtime outcome selection blind manifest hash mismatch")
    outcome_by_case = {
        case.episode_id: case for case in outcome_selection.cases
    }
    if set(outcome_by_case) != set(paired.expected_case_ids):
        raise ValueError("runtime outcome case population differs from predictions")
    observations: list[QualityCaseObservation] = []
    outcome_payloads: dict[str, bytes] = {}
    outcome_hashes: dict[str, str] = {}
    for case_id, outcome_case in outcome_by_case.items():
        outcome_path = _resolve_reference(root, outcome_case.outcome_ledger)
        outcome_bytes = _read_verified_bytes_reference(
            outcome_case.outcome_ledger,
            outcome_path,
        )
        outcome_payloads[case_id] = outcome_bytes
        outcome_hashes[case_id] = sha256_bytes(outcome_bytes)
    for seal in paired.seals:
        prediction = predictions[(seal.case_id, seal.variant_id)]
        observations.append(
            _quality_case_observation(
                root,
                seal=seal,
                prediction=prediction,
                outcome_bytes=outcome_payloads[seal.case_id],
                prediction_run_id=paired.run_id,
                shared_stage=shared_stage_by_case[seal.case_id],
            )
        )
    report = build_quality_score_report(
        paired=paired,
        paired_manifest_sha256=paired_sha256,
        observations=observations,
        outcome_hashes=outcome_hashes,
    )
    output_dir = paired_prediction_manifest_path.parent / "scoring"
    report_path = output_dir / "runtime_variant_score_report.json"
    markdown_path = output_dir / "runtime_variant_score_report.md"
    write_json(report_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(render_quality_score(report), encoding="utf-8")
    return QualityRuntimeScoreResult(
        report=report,
        report_path=report_path,
        markdown_path=markdown_path,
    )


def _quality_case_observation(
    root: Path,
    *,
    seal: PredictionSeal,
    prediction: BlindPrediction,
    outcome_bytes: bytes,
    prediction_run_id: str,
    shared_stage: SharedStageObservation,
) -> QualityCaseObservation:
    context_path = _resolve_reference(root, seal.context_manifest)
    context = ContextManifest.model_validate(
        _read_verified_json_reference(seal.context_manifest, context_path)
    )
    shared_artifact = context.shared_pre_retrieval_context_artifact
    if not shared_artifact:
        raise ValueError("quality observation has no shared context artifact")
    shared_reference = QualityArtifactReference(
        artifact_path=shared_artifact,
        sha256=seal.shared_context_sha256,
    )
    shared_path = _resolve_reference(root, shared_reference)
    shared_context = SharedPreRetrievalContext.model_validate(
        _read_verified_json_reference(shared_reference, shared_path)
    )
    shared_safety = _verify_final_synthesis_contract_and_shared_digest(
        root=root,
        context=context,
        shared_context=shared_context,
    )
    d_minus_one_path = _resolve_reference(root, seal.d_minus_one_context)
    d_minus_one = SharedDMinusOneContext.model_validate(
        _read_verified_json_reference(seal.d_minus_one_context, d_minus_one_path)
    )
    metrics = _prediction_metrics(
        prediction,
        None,
        truth_bytes=outcome_bytes,
        evaluation_universe_tickers=d_minus_one.candidate_universe,
        probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    )
    metrics["generated_candidate_tickers"] = (
        _generated_candidate_tickers_from_final_context(
            shared_safety["verified_final_synthesis_artifact"]
        )
    )
    metrics["final_memory_citation_rate"] = _final_memory_citation_rate(
        prediction
    )
    trace_stats = _trace_stats(
        root,
        context,
        expected_prediction_id=prediction.prediction_id,
        require_complete_provenance=True,
    )
    raw_retrieval = trace_stats.get("retrieval_observation")
    if raw_retrieval is None:
        raise ValueError("quality observation has no typed retrieval ledger")
    retrieval = RetrievalCaseObservation.model_validate(raw_retrieval)
    daily_artifact = context.daily_memory_context_artifact
    daily_sha256 = context.daily_memory_context_sha256
    if not daily_artifact or not daily_sha256:
        raise ValueError("quality observation has no daily memory context binding")
    daily_path = _resolve_reference(
        root,
        QualityArtifactReference(
            artifact_path=daily_artifact,
            sha256=daily_sha256,
        ),
    )
    daily = DailyMemoryContext.model_validate(
        _read_verified_json_reference(
            QualityArtifactReference(
                artifact_path=daily_artifact,
                sha256=daily_sha256,
            ),
            daily_path,
            text_hash=True,
        )
    )
    allowed_context_record_ids = set(daily.supporting_record_ids)
    allowed_context_record_ids.update(daily.contradicting_record_ids)
    allowed_context_record_ids.update(daily.unexplained_record_ids)
    prediction_record_ids = _prediction_memory_record_ids(prediction)
    runtime_final_record_ids = set(retrieval.final_cited_record_ids)
    legacy_final_record_ids = (
        prediction_record_ids if seal.variant_id == "V0" else set()
    )
    orphan_record_ids = prediction_record_ids - allowed_context_record_ids
    expected_final_targets = {
        f"candidate:{candidate.rank}:{candidate.ticker}"
        for candidate in prediction.candidates
    }
    observed_final_targets = {
        str(value) for value in trace_stats["runtime_final_candidate_ids"]
    }
    orphan_final_targets = observed_final_targets - expected_final_targets
    citation_closure = CitationClosureObservation(
        prediction_memory_record_ids=sorted(prediction_record_ids),
        allowed_context_record_ids=sorted(allowed_context_record_ids),
        runtime_final_cited_record_ids=sorted(runtime_final_record_ids),
        legacy_final_cited_record_ids=sorted(legacy_final_record_ids),
        orphan_record_ids=sorted(orphan_record_ids),
        orphan_final_target_ids=sorted(orphan_final_targets),
        closure_verified=not orphan_record_ids and not orphan_final_targets,
    )
    wrong_snapshot_count = int(
        retrieval.memory_snapshot_id != seal.memory_snapshot_id
    )
    safety = SafetyObservation(
        future_record_count=int(trace_stats["future_record_count"]),
        blind_web_call_count=(
            context.blind_web_search_call_count
            + int(trace_stats["blind_web_call_count"])
        ),
        online_full_scan_count=int(trace_stats["online_full_scan_count"]),
        outcome_reference_count_during_prediction=seal.outcome_reference_count,
        orphan_citation_count=(
            len(orphan_record_ids) + len(orphan_final_targets)
        ),
        wrong_snapshot_count=wrong_snapshot_count,
        snapshot_closure_verified=wrong_snapshot_count == 0,
        forbidden_shared_key_count=int(
            shared_safety["forbidden_shared_key_count"]
        ),
        shared_digest_closure_verified=bool(
            shared_safety["shared_digest_closure_verified"]
        ),
    )
    efficiency = _validated_attempt_efficiency(
        root,
        seal=seal,
        expected_prediction_run_id=prediction_run_id,
    )
    return QualityCaseObservation(
        case_id=seal.case_id,
        trade_date=prediction.trade_date,
        variant_id=seal.variant_id,
        metrics=metrics,
        retrieval=retrieval,
        citation_closure=citation_closure,
        safety=safety,
        efficiency=efficiency,
        shared_stage=shared_stage,
        shared_context_sha256=seal.shared_context_sha256,
        evaluation_universe_sha256=str(metrics["evaluation_universe_sha256"]),
        evaluation_universe_count=int(metrics["evaluation_universe_count"]),
        population_universe_sha256=str(metrics["population_universe_sha256"]),
        population_universe_count=int(metrics["population_count"]),
        market_universe_policy_version=QUALITY_MARKET_UNIVERSE_POLICY_VERSION,
        brier_population_policy_version=(
            QUALITY_BRIER_POPULATION_POLICY_VERSION
        ),
        probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    )


def _prediction_memory_record_ids(prediction: BlindPrediction) -> set[str]:
    record_ids: set[str] = set()
    for candidate in prediction.candidates:
        record_ids.update(candidate.memory_record_ids)
        record_ids.update(candidate.prior_positive_record_ids)
        record_ids.update(candidate.prior_negative_record_ids)
    for sector in prediction.dominant_sectors:
        record_ids.update(sector.supporting_record_ids)
        record_ids.update(sector.contradicting_record_ids)
    return record_ids


def _generated_candidate_tickers_from_final_context(
    artifact: object,
) -> list[str]:
    verified_artifact = FinalSynthesisContextArtifact.model_validate(artifact)
    candidate_research = verified_artifact.payload.get("candidate_research")
    candidates = (
        candidate_research.get("candidates")
        if isinstance(candidate_research, dict)
        else None
    )
    if not isinstance(candidates, list):
        raise ValueError("quality final synthesis has no generated candidate set")
    tickers: list[str] = []
    for row in candidates:
        ticker = row.get("ticker") if isinstance(row, dict) else None
        if (
            not isinstance(ticker, str)
            or len(ticker) != 6
            or not ticker.isdigit()
        ):
            raise ValueError("quality generated candidate ticker is invalid")
        tickers.append(ticker)
    if len(tickers) != len(set(tickers)):
        raise ValueError("quality generated candidate tickers are duplicated")
    return tickers


def _verify_paired_prediction_closure(
    root: Path,
    *,
    paired: PairedPredictionManifest,
    blind_selection: BlindRuntimeSelection,
) -> dict[tuple[str, str], BlindPrediction]:
    case_by_id = {case.episode_id: case for case in blind_selection.cases}
    if set(case_by_id) != set(paired.expected_case_ids):
        raise ValueError("paired predictions differ from the blind case population")
    predictions: dict[tuple[str, str], BlindPrediction] = {}
    for seal in paired.seals:
        case = case_by_id[seal.case_id]
        if (
            seal.cutoff_at != case.cutoff_at
            or seal.blind_input_manifest != case.blind_input_manifest
            or seal.news_sha256 != case.news_sha256
        ):
            raise ValueError("prediction seal differs from its blind input case")
        blind_input_path = _resolve_blind_input_reference(
            root,
            seal.blind_input_manifest,
        )
        blind_input = SealedBlindCaseInputManifest.model_validate(
            _read_verified_json_reference(
                seal.blind_input_manifest,
                blind_input_path,
            )
        )
        _verify_sealed_input_location(
            blind_input,
            manifest_reference=seal.blind_input_manifest,
            manifest_path=blind_input_path,
        )
        if (
            blind_input.episode_id != case.episode_id
            or blind_input.trade_date != case.trade_date
            or as_kst(blind_input.cutoff_at) != as_kst(case.cutoff_at)
            or blind_input.news_csv.sha256 != case.news_sha256
        ):
            raise ValueError("sealed blind input closure is invalid")
        news_path = _resolve_blind_input_reference(root, blind_input.news_csv)
        if news_path.parent != blind_input_path.parent:
            raise ValueError("sealed blind news escaped its input package")
        _verify_reference(blind_input.news_csv, news_path)

        brain_path = _resolve_reference(root, seal.brain_manifest)
        coverage_path = _resolve_reference(root, seal.coverage_manifest)
        prediction_path = _resolve_reference(root, seal.prediction)
        context_path = _resolve_reference(root, seal.context_manifest)
        brain = _read_verified_json_reference(seal.brain_manifest, brain_path)
        coverage = _read_verified_json_reference(
            seal.coverage_manifest,
            coverage_path,
        )
        if (
            not isinstance(brain, dict)
            or brain.get("build_mode") != "llm-full"
            or brain.get("production_memory_snapshot_id")
            != seal.memory_snapshot_id
            or not isinstance(coverage, dict)
            or coverage.get("coverage_scope") != "EVALUATION_REPLAY_BUILD"
        ):
            raise ValueError("prediction seal brain or coverage closure is invalid")
        prediction = BlindPrediction.model_validate(
            _read_verified_json_reference(seal.prediction, prediction_path)
        )
        context = ContextManifest.model_validate(
            _read_verified_json_reference(seal.context_manifest, context_path)
        )
        if (
            prediction.trade_date != case.trade_date
            or as_kst(prediction.cutoff_at) != as_kst(case.cutoff_at)
            or prediction.context_manifest_id != context.run_id
            or context.trade_date != case.trade_date
            or as_kst(context.cutoff_at) != as_kst(case.cutoff_at)
            or context.news_sha256 != case.news_sha256
            or context.parsed_news_root_sha256
            != seal.parsed_news_root_sha256
            or context.prediction_input_boundary_version
            != PREDICTION_INPUT_BOUNDARY_VERSION
            or context.sealed_blind_input_manifest_sha256
            != case.blind_input_manifest.sha256
            or context.brain_version != brain.get("brain_version")
            or context.errors
            or not context.no_d_outcome_exposed
            or context.blind_web_search_call_count
            or context.blind_current_price_access_count
        ):
            raise ValueError("prediction context closure is invalid")
        if (
            context.prediction_artifact != seal.prediction.artifact_path
            or context.prediction_sha256 != seal.prediction.sha256
            or context.shared_pre_retrieval_context_sha256
            != seal.shared_context_sha256
            or not context.shared_pre_retrieval_context_artifact
        ):
            raise ValueError("prediction context artifact bindings are invalid")
        shared_path = _resolve_reference(
            root,
            QualityArtifactReference(
                artifact_path=context.shared_pre_retrieval_context_artifact,
                sha256=seal.shared_context_sha256,
            ),
        )
        shared_reference = QualityArtifactReference(
            artifact_path=context.shared_pre_retrieval_context_artifact,
            sha256=seal.shared_context_sha256,
        )
        shared_context = SharedPreRetrievalContext.model_validate(
            _read_verified_json_reference(shared_reference, shared_path)
        )
        if (
            shared_context.d_minus_one_safe_context != seal.d_minus_one_context
            or shared_context.parsed_news_root_sha256
            != seal.parsed_news_root_sha256
        ):
            raise ValueError("prediction shared D-minus-one binding is invalid")
        _verify_final_synthesis_contract_and_shared_digest(
            root=root,
            context=context,
            shared_context=shared_context,
        )
        d_minus_one_path = _resolve_reference(root, seal.d_minus_one_context)
        d_minus_one_context = SharedDMinusOneContext.model_validate(
            _read_verified_json_reference(
                seal.d_minus_one_context,
                d_minus_one_path,
            )
        )
        d_minus_one_payload_sha256 = sha256_text(
            canonical_json(d_minus_one_context.model_dump(mode="json"))
        )
        if (
            context.d_minus_one_context_artifact
            != seal.d_minus_one_context.artifact_path
            or context.d_minus_one_context_sha256
            != seal.d_minus_one_context_sha256
            or seal.d_minus_one_context_sha256
            != seal.d_minus_one_context.sha256
            or context.d_minus_one_candidate_universe_root_sha256
            != seal.d_minus_one_candidate_universe_root_sha256
            or context.d_minus_one_snapshot_root_sha256
            != seal.d_minus_one_snapshot_root_sha256
            or d_minus_one_context.candidate_universe_root_sha256
            != seal.d_minus_one_candidate_universe_root_sha256
            or d_minus_one_context.snapshot_root_sha256
            != seal.d_minus_one_snapshot_root_sha256
            or context.d_minus_one_source_revision_sha256
            != seal.d_minus_one_source_revision_sha256
            or context.d_minus_one_snapshot_session_date
            != seal.d_minus_one_snapshot_session_date
            or d_minus_one_context.source_revision_sha256
            != seal.d_minus_one_source_revision_sha256
            or d_minus_one_context.snapshot_session_date
            != seal.d_minus_one_snapshot_session_date
            or context.d_minus_one_payload_sha256
            != seal.d_minus_one_payload_sha256
            or context.d_minus_one_consumed_payload_sha256
            != seal.d_minus_one_consumed_payload_sha256
            or d_minus_one_payload_sha256
            != seal.d_minus_one_payload_sha256
            or d_minus_one_context.trade_date != case.trade_date
            or as_kst(d_minus_one_context.cutoff_at) != as_kst(case.cutoff_at)
            or d_minus_one_context.allowed_through
            != case.trade_date - timedelta(days=1)
            or context.price_snapshot.allowed_through
            != d_minus_one_context.allowed_through
            or context.price_snapshot.source_name
            != d_minus_one_context.source_name
            or context.price_snapshot.source_ref
            != d_minus_one_context.source_ref
            or d_minus_one_context.d_day_access_count
            or d_minus_one_context.outcome_access_count
            or context.blind_price_repository_access_count
            != d_minus_one_context.price_repository_access_count
        ):
            raise ValueError("prediction D-minus-one context closure is invalid")
        consumed_d_minus_one_projection = (
            _verify_final_synthesis_d_minus_one_consumption(
                root=root,
                context=context,
                expected_context=d_minus_one_context,
            )
        )
        _verify_d_minus_one_projection_seal_binding(
            projection=consumed_d_minus_one_projection,
            context=context,
            seal=seal,
        )
        trace_stats = _trace_stats(
            root,
            context,
            expected_prediction_id=prediction.prediction_id,
            require_complete_provenance=True,
        )
        if (
            int(trace_stats["future_record_count"])
            or int(trace_stats["online_full_scan_count"])
            or int(trace_stats["final_cited_record_count"])
            != seal.final_citation_count
            or trace_stats["memory_snapshot_id"] != seal.memory_snapshot_id
        ):
            raise ValueError("prediction retrieval trace closure is invalid")
        _validated_attempt_efficiency(
            root,
            seal=seal,
            expected_prediction_run_id=paired.run_id,
        )
        predictions[(seal.case_id, seal.variant_id)] = prediction
    if set(predictions) != {
        (case_id, variant_id)
        for case_id in paired.expected_case_ids
        for variant_id in paired.expected_variant_ids
    }:
        raise ValueError("paired prediction closure is incomplete")
    return predictions


def _verify_completed_prediction_seal_identity(
    *,
    root: Path,
    seal: PredictionSeal,
    case: BlindRuntimeCase,
    shared_context_sha256: str,
    parsed_news_root_sha256: str,
    d_minus_one_reference: QualityArtifactReference,
    d_minus_one_context: SharedDMinusOneContext,
    d_minus_one_payload_sha256: str,
    candidate_universe_policy_sha256: str,
    brain_manifest: QualityArtifactReference,
    coverage_manifest: QualityArtifactReference,
    memory_snapshot_id: str,
    prediction_run_id: str | None = None,
    variant_architecture_sha256: str | None = None,
) -> None:
    if (
        seal.case_id != case.episode_id
        or (
            variant_architecture_sha256 is not None
            and seal.variant_architecture_sha256
            != variant_architecture_sha256
        )
        or as_kst(seal.cutoff_at) != as_kst(case.cutoff_at)
        or seal.blind_input_manifest != case.blind_input_manifest
        or seal.news_sha256 != case.news_sha256
        or seal.parsed_news_root_sha256 != parsed_news_root_sha256
        or seal.shared_context_sha256 != shared_context_sha256
        or seal.brain_manifest != brain_manifest
        or seal.coverage_manifest != coverage_manifest
        or seal.memory_snapshot_id != memory_snapshot_id
        or seal.d_minus_one_context != d_minus_one_reference
        or seal.d_minus_one_context_sha256 != d_minus_one_reference.sha256
        or seal.d_minus_one_candidate_universe_root_sha256
        != d_minus_one_context.candidate_universe_root_sha256
        or seal.d_minus_one_snapshot_root_sha256
        != d_minus_one_context.snapshot_root_sha256
        or seal.d_minus_one_source_revision_sha256
        != d_minus_one_context.source_revision_sha256
        or seal.d_minus_one_snapshot_session_date
        != d_minus_one_context.snapshot_session_date
        or seal.d_minus_one_payload_sha256 != d_minus_one_payload_sha256
        or seal.candidate_universe_policy_sha256
        != candidate_universe_policy_sha256
    ):
        raise ValueError("completed quality prediction seal identity drifted")
    context_path = _resolve_reference(root, seal.context_manifest)
    context = ContextManifest.model_validate(
        _read_verified_json_reference(seal.context_manifest, context_path)
    )
    prediction = BlindPrediction.model_validate(
        _read_verified_json_reference(
            seal.prediction,
            _resolve_reference(root, seal.prediction),
        )
    )
    if (
        context.parsed_news_root_sha256 != parsed_news_root_sha256
        or context.shared_pre_retrieval_context_sha256
        != shared_context_sha256
        or context.d_minus_one_context_artifact
        != d_minus_one_reference.artifact_path
        or context.d_minus_one_context_sha256
        != d_minus_one_reference.sha256
        or context.d_minus_one_candidate_universe_root_sha256
        != d_minus_one_context.candidate_universe_root_sha256
        or context.d_minus_one_snapshot_root_sha256
        != d_minus_one_context.snapshot_root_sha256
        or context.d_minus_one_source_revision_sha256
        != d_minus_one_context.source_revision_sha256
        or context.d_minus_one_snapshot_session_date
        != d_minus_one_context.snapshot_session_date
        or context.d_minus_one_payload_sha256
        != d_minus_one_payload_sha256
    ):
        raise ValueError("completed quality prediction context identity drifted")
    consumed_projection = _verify_final_synthesis_d_minus_one_consumption(
        root=root,
        context=context,
        expected_context=d_minus_one_context,
    )
    _verify_d_minus_one_projection_seal_binding(
        projection=consumed_projection,
        context=context,
        seal=seal,
    )
    shared_artifact = context.shared_pre_retrieval_context_artifact
    if not shared_artifact:
        raise ValueError("completed quality prediction shared context is missing")
    shared_reference = QualityArtifactReference(
        artifact_path=shared_artifact,
        sha256=seal.shared_context_sha256,
    )
    shared_path = _resolve_reference(root, shared_reference)
    shared_context = SharedPreRetrievalContext.model_validate(
        _read_verified_json_reference(shared_reference, shared_path)
    )
    _verify_final_synthesis_contract_and_shared_digest(
        root=root,
        context=context,
        shared_context=shared_context,
    )
    trace_stats = _trace_stats(
        root,
        context,
        expected_prediction_id=prediction.prediction_id,
        require_complete_provenance=True,
    )
    if (
        trace_stats["memory_snapshot_id"] != memory_snapshot_id
        or int(trace_stats["future_record_count"])
        or int(trace_stats["blind_web_call_count"])
        or int(trace_stats["online_full_scan_count"])
        or int(trace_stats["final_cited_record_count"])
        != seal.final_citation_count
    ):
        raise ValueError("completed quality prediction retrieval closure drifted")
    _validated_attempt_efficiency(
        root,
        seal=seal,
        expected_prediction_run_id=prediction_run_id,
    )


def _validated_attempt_efficiency(
    root: Path,
    *,
    seal: PredictionSeal,
    expected_prediction_run_id: str | None,
) -> RuntimeEfficiencyObservation:
    efficiency = seal.efficiency
    path_text = efficiency.get("attempt_ledger_artifact_path")
    sha256 = efficiency.get("attempt_ledger_sha256")
    if not isinstance(path_text, str) or not isinstance(sha256, str):
        raise ValueError("prediction seal has no attempt ledger binding")
    reference = QualityArtifactReference(
        artifact_path=path_text,
        sha256=sha256,
    )
    path = _resolve_reference(root, reference)
    ledger = _read_verified_json_reference(reference, path)
    if not isinstance(ledger, dict):
        raise ValueError("prediction attempt ledger is invalid")
    run_id = ledger.get("run_id")
    if expected_prediction_run_id is None:
        expected_prediction_run_id = path.parents[3].name
    if (
        ledger.get("schema_version") != QUALITY_ATTEMPT_LEDGER_VERSION
        or ledger.get("prediction_code_version")
        != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
        or run_id != expected_prediction_run_id
        or ledger.get("case_id") != seal.case_id
        or ledger.get("variant_id") != seal.variant_id
        or any(
            ledger.get(key) != efficiency.get(key)
            for key in (
                "attempt_count",
                "recovered_interrupted_attempt_count",
                "contains_recovered_attempts",
                "elapsed_accounting_status",
                "elapsed_exact_completed_seconds",
                "elapsed_lower_bound_seconds",
                "elapsed_upper_bound_seconds",
                "runtime_metrics",
                "runtime_metric_statuses",
                "runtime_metrics_accounting_status",
            )
        )
        or not isinstance(ledger.get("attempts"), list)
        or len(ledger["attempts"]) != ledger.get("attempt_count")
    ):
        raise ValueError("prediction attempt ledger identity or aggregate drifted")
    for attempt in ledger["attempts"]:
        if not isinstance(attempt, dict):
            raise ValueError("prediction attempt ledger row is invalid")
        for key in ("start_receipt", "completion_receipt"):
            raw_reference = attempt.get(key)
            if raw_reference is None and key == "completion_receipt":
                continue
            receipt = QualityArtifactReference.model_validate(raw_reference)
            _verify_reference(receipt, _resolve_reference(root, receipt))
    _validate_attempt_ledger_aggregates(root, ledger, shared=False)
    return RuntimeEfficiencyObservation.model_validate(
        {
            **{
                key: efficiency[key]
                for key in (
                    "elapsed_accounting_status",
                    "elapsed_exact_completed_seconds",
                    "elapsed_lower_bound_seconds",
                    "elapsed_upper_bound_seconds",
                    "contains_recovered_attempts",
                    "recovered_interrupted_attempt_count",
                    "runtime_metrics",
                    "runtime_metric_statuses",
                    "runtime_metrics_accounting_status",
                    "attempt_count",
                )
            },
            "attempt_ledger_sha256": sha256,
        }
    )


def _validated_shared_preparation_observation(
    root: Path,
    *,
    reference: QualityArtifactReference,
    expected_prediction_run_id: str,
    expected_case_id: str,
    expected_shared_context_sha256: str,
) -> SharedStageObservation:
    path = _resolve_reference(root, reference)
    ledger = _read_verified_json_reference(reference, path)
    if (
        not isinstance(ledger, dict)
        or ledger.get("schema_version")
        != "nslab.quality_shared_preparation_ledger.v1"
        or ledger.get("prediction_code_version")
        != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
        or ledger.get("run_id") != expected_prediction_run_id
        or ledger.get("case_id") != expected_case_id
        or ledger.get("shared_context_sha256")
        != expected_shared_context_sha256
    ):
        raise ValueError("shared preparation ledger identity drifted")
    _validate_attempt_ledger_aggregates(root, ledger, shared=True)
    return SharedStageObservation.model_validate(
        {
            **{
                key: ledger[key]
                for key in (
                    "elapsed_accounting_status",
                    "elapsed_exact_completed_seconds",
                    "elapsed_lower_bound_seconds",
                    "elapsed_upper_bound_seconds",
                    "contains_recovered_attempts",
                    "recovered_interrupted_attempt_count",
                    "runtime_metrics",
                    "runtime_metric_statuses",
                    "runtime_metrics_accounting_status",
                    "attempt_count",
                    "build_attempt_count",
                    "cache_load_attempt_count",
                )
            },
            "attempt_ledger_sha256": reference.sha256,
        }
    )


def _validate_attempt_ledger_aggregates(
    root: Path,
    ledger: dict[str, Any],
    *,
    shared: bool,
) -> None:
    attempts = ledger.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("attempt ledger has no attempt rows")
    recovery_count = 0
    build_count = 0
    cache_count = 0
    for expected_number, row in enumerate(attempts, start=1):
        if (
            not isinstance(row, dict)
            or row.get("attempt_number") != expected_number
            or not isinstance(row.get("attempt_id"), str)
            or not isinstance(row.get("runtime_metrics"), dict)
            or not isinstance(row.get("runtime_metric_statuses"), dict)
            or set(row["runtime_metrics"]) != set(QUALITY_RUNTIME_COUNTER_KEYS)
            or set(row["runtime_metric_statuses"])
            != set(QUALITY_RUNTIME_COUNTER_KEYS)
        ):
            raise ValueError("attempt ledger row structure is invalid")
        status = row.get("status")
        recovered = status == "RECOVERED_INTERRUPTED_BOUNDED"
        exact_seconds = row.get("elapsed_exact_seconds")
        lower = row.get("elapsed_lower_bound_seconds")
        upper = row.get("elapsed_upper_bound_seconds")
        if (
            not isinstance(lower, int | float)
            or not isinstance(upper, int | float)
            or not math.isfinite(float(lower))
            or not math.isfinite(float(upper))
            or float(lower) < 0.0
            or float(lower) > float(upper)
            or (recovered and exact_seconds is not None)
            or (
                not recovered
                and (
                    not isinstance(exact_seconds, int | float)
                    or float(exact_seconds) != float(lower)
                    or float(exact_seconds) != float(upper)
                )
            )
            or row.get("elapsed_accounting_status")
            != ("BOUNDED_RECOVERY" if recovered else "EXACT")
        ):
            raise ValueError("attempt ledger elapsed row is invalid")
        allowed_statuses = (
            {
                "BUILD_COMPLETED_EXACT",
                "CACHE_LOAD_COMPLETED_EXACT",
                "RECOVERED_INTERRUPTED_BOUNDED",
            }
            if shared
            else {
                "COMPLETED_RECEIPT_EXACT",
                "RECOVERED_INTERRUPTED_BOUNDED",
            }
        )
        if status not in allowed_statuses:
            raise ValueError("attempt ledger status is invalid")
        recovery_count += int(recovered)
        build_count += int(status == "BUILD_COMPLETED_EXACT")
        cache_count += int(status == "CACHE_LOAD_COMPLETED_EXACT")
        start_reference = QualityArtifactReference.model_validate(
            row.get("start_receipt")
        )
        _verify_reference(
            start_reference,
            _resolve_reference(root, start_reference),
        )
        completion_raw = row.get("completion_receipt")
        if recovered:
            if completion_raw is not None:
                raise ValueError("recovered attempt unexpectedly has a completion")
        else:
            completion_reference = QualityArtifactReference.model_validate(
                completion_raw
            )
            _verify_reference(
                completion_reference,
                _resolve_reference(root, completion_reference),
            )
    metrics, metric_statuses = _aggregate_attempt_runtime_metrics(attempts)
    exact_completed = sum(
        float(row.get("elapsed_exact_seconds") or 0.0) for row in attempts
    )
    lower_bound = sum(
        float(row["elapsed_lower_bound_seconds"]) for row in attempts
    )
    upper_bound = sum(
        float(row["elapsed_upper_bound_seconds"]) for row in attempts
    )
    contains_recovered = recovery_count > 0
    unavailable = any(
        status in {"UNAVAILABLE", "PARTIAL_LOWER_BOUND"}
        for status in metric_statuses.values()
    )
    expected_accounting = (
        "RECOVERED_PARTIAL"
        if contains_recovered and unavailable
        else "RECOVERED_LOWER_BOUND"
        if contains_recovered
        else "PARTIAL_UNAVAILABLE"
        if unavailable
        else "EXACT"
    )
    if any(
        (
            ledger.get("attempt_count") != len(attempts),
            ledger.get("recovered_interrupted_attempt_count") != recovery_count,
            ledger.get("contains_recovered_attempts") is not contains_recovered,
            ledger.get("elapsed_accounting_status")
            != ("BOUNDED_RECOVERY" if contains_recovered else "EXACT"),
            ledger.get("elapsed_exact_completed_seconds") != exact_completed,
            ledger.get("elapsed_lower_bound_seconds") != lower_bound,
            ledger.get("elapsed_upper_bound_seconds") != upper_bound,
            ledger.get("runtime_metrics") != metrics,
            ledger.get("runtime_metric_statuses") != metric_statuses,
            ledger.get("runtime_metrics_accounting_status")
            != expected_accounting,
            shared and ledger.get("build_attempt_count") != build_count,
            shared and ledger.get("cache_load_attempt_count") != cache_count,
        )
    ):
        raise ValueError("attempt ledger aggregate is stale")


def _verify_final_synthesis_d_minus_one_consumption(
    *,
    root: Path,
    context: ContextManifest,
    expected_context: SharedDMinusOneContext,
) -> DMinusOnePromptProjection:
    artifact_path_text = context.final_synthesis_context_artifact
    artifact_sha256 = context.final_synthesis_context_sha256
    if not artifact_path_text or not artifact_sha256:
        raise ValueError("prediction final synthesis context artifact is missing")
    artifact_reference = QualityArtifactReference(
        artifact_path=artifact_path_text,
        sha256=artifact_sha256,
    )
    artifact_path = _resolve_reference(root, artifact_reference)
    artifact = FinalSynthesisContextArtifact.model_validate(
        _read_verified_json_reference(
            artifact_reference,
            artifact_path,
            text_hash=True,
        )
    )
    if (
        artifact.run_id != context.run_id
        or artifact.payload_sha256
        != sha256_text(canonical_json(artifact.payload))
    ):
        raise ValueError("prediction final synthesis context identity drifted")
    consumed_payload = artifact.payload.get("d_minus_one_market_data")
    candidate_research = artifact.payload.get("candidate_research")
    if not isinstance(consumed_payload, dict) or not isinstance(
        candidate_research, dict
    ):
        raise ValueError("prediction final synthesis omitted its D-1 projection inputs")
    projection = DMinusOnePromptProjection.model_validate(consumed_payload)
    preliminary_prediction = BlindPrediction.model_validate(candidate_research)
    consumed_sha256 = sha256_text(canonical_json(consumed_payload))
    full_payload_sha256 = sha256_text(
        canonical_json(expected_context.model_dump(mode="json"))
    )
    context_artifact = context.d_minus_one_context_artifact
    context_sha256 = context.d_minus_one_context_sha256
    if not context_artifact or not context_sha256:
        raise ValueError("prediction context omitted the full sealed D-1 artifact")
    context_reference = QualityArtifactReference(
        artifact_path=context_artifact,
        sha256=context_sha256,
    )
    expected_projection = DailyAnalyzer._build_d_minus_one_prompt_projection(
        context=expected_context,
        context_reference=context_reference,
        candidates=preliminary_prediction.candidates,
    )
    if (
        projection != expected_projection
        or projection.full_context != context_reference
        or projection.full_payload_sha256 != full_payload_sha256
        or context.d_minus_one_payload_sha256 != full_payload_sha256
        or context.d_minus_one_consumed_payload_sha256 != consumed_sha256
        or context.d_minus_one_projection_status != "BOUND"
        or context.d_minus_one_projection_policy != projection.projection_policy
        or context.d_minus_one_projection_root_sha256
        != projection.projection_root_sha256
        or context.d_minus_one_projection_requested_ticker_count
        != len(projection.requested_tickers)
        or context.d_minus_one_projection_snapshot_count
        != len(projection.snapshots)
        or context.d_minus_one_projection_missing_ticker_count
        != len(projection.missing_tickers)
    ):
        raise ValueError(
            "prediction final synthesis consumed a different D-1 prompt projection"
        )
    return projection


def _verify_d_minus_one_projection_seal_binding(
    *,
    projection: DMinusOnePromptProjection,
    context: ContextManifest,
    seal: PredictionSeal,
) -> None:
    consumed_sha256 = sha256_text(
        canonical_json(projection.model_dump(mode="json"))
    )
    if (
        seal.d_minus_one_consumed_payload_sha256 != consumed_sha256
        or seal.d_minus_one_projection_policy != projection.projection_policy
        or seal.d_minus_one_projection_root_sha256
        != projection.projection_root_sha256
        or seal.d_minus_one_projection_requested_ticker_count
        != len(projection.requested_tickers)
        or seal.d_minus_one_projection_snapshot_count
        != len(projection.snapshots)
        or seal.d_minus_one_projection_missing_ticker_count
        != len(projection.missing_tickers)
        or context.d_minus_one_consumed_payload_sha256 != consumed_sha256
        or context.d_minus_one_projection_policy != projection.projection_policy
        or context.d_minus_one_projection_root_sha256
        != projection.projection_root_sha256
        or context.d_minus_one_projection_requested_ticker_count
        != len(projection.requested_tickers)
        or context.d_minus_one_projection_snapshot_count
        != len(projection.snapshots)
        or context.d_minus_one_projection_missing_ticker_count
        != len(projection.missing_tickers)
    ):
        raise ValueError("prediction D-1 prompt projection seal is invalid")


def _verify_final_synthesis_contract_and_shared_digest(
    *,
    root: Path,
    context: ContextManifest,
    shared_context: SharedPreRetrievalContext,
) -> dict[str, Any]:
    artifact_path_text = context.final_synthesis_context_artifact
    artifact_sha256 = context.final_synthesis_context_sha256
    if not artifact_path_text or not artifact_sha256:
        raise ValueError("prediction final synthesis context artifact is missing")
    artifact_reference = QualityArtifactReference(
        artifact_path=artifact_path_text,
        sha256=artifact_sha256,
    )
    artifact_path = _resolve_reference(root, artifact_reference)
    raw_artifact = _read_verified_json_reference(
        artifact_reference,
        artifact_path,
        text_hash=True,
    )
    artifact = FinalSynthesisContextArtifact.model_validate(raw_artifact)
    manifest_payload = context.model_dump(mode="json")
    artifact_payload = artifact.model_dump(mode="json")
    if not final_synthesis_context_contract_verified(
        manifest_payload,
        artifact_payload,
    ) or not final_synthesis_phase7_artifacts_compatible(
        root,
        manifest_payload,
        artifact_payload,
    ):
        raise ValueError("prediction final synthesis full contract is invalid")
    digest_path = _resolve_reference(root, shared_context.downstream_digest)
    raw_digest = _read_verified_json_reference(
        shared_context.downstream_digest,
        digest_path,
    )
    digest = SharedDownstreamDigest.model_validate(raw_digest)
    if digest.context_id != shared_context.context_id:
        raise ValueError("shared downstream digest identity drifted")
    consumed_digest = artifact.payload.get("shared_current_event_digest")
    expected_digest = digest.model_dump(mode="json")
    if consumed_digest != expected_digest:
        raise ValueError("final synthesis did not consume the exact shared digest")
    reject_forbidden_blind_payload_keys(raw_digest)
    return {
        "shared_digest_closure_verified": True,
        "forbidden_shared_key_count": 0,
        "shared_digest_sha256": shared_context.downstream_digest.sha256,
        "verified_final_synthesis_artifact": artifact,
    }


def _load_or_create_prediction_manifest(
    *,
    root: Path,
    path: Path,
    run_id: str,
    profile: QualityEvaluationProfile,
    blind_selection_path: Path,
    expected_case_ids: list[str],
    expected_variant_architecture_sha256: dict[str, str],
    shared_preparation_ledgers: dict[str, QualityArtifactReference],
) -> PairedPredictionManifest:
    blind_reference = _path_reference(root, blind_selection_path)
    if path.exists():
        manifest = PairedPredictionManifest.model_validate(read_json(path))
        if (
            manifest.run_id != run_id
            or manifest.prediction_code_version
            != QUALITY_RUNTIME_PREDICTION_CODE_VERSION
            or manifest.profile != profile
            or manifest.blind_selection != blind_reference
            or manifest.expected_case_ids != expected_case_ids
            or tuple(manifest.expected_variant_ids) != QUALITY_RUNTIME_VARIANTS
            or manifest.expected_variant_architecture_sha256
            != expected_variant_architecture_sha256
        ):
            raise ValueError("existing quality prediction progress identity drifted")
        return PairedPredictionManifest.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "shared_preparation_ledgers": {
                    key: value.model_dump(mode="json")
                    for key, value in sorted(
                        shared_preparation_ledgers.items()
                    )
                },
            }
        )
    return PairedPredictionManifest(
        run_id=run_id,
        prediction_code_version=QUALITY_RUNTIME_PREDICTION_CODE_VERSION,
        profile=profile,
        blind_selection=blind_reference,
        expected_case_ids=expected_case_ids,
        expected_variant_ids=list(QUALITY_RUNTIME_VARIANTS),
        expected_variant_architecture_sha256=(
            expected_variant_architecture_sha256
        ),
        shared_preparation_ledgers=shared_preparation_ledgers,
        seals=[],
        paired_case_ids=[],
        all_predictions_sealed=False,
    )
def _prediction_manifest_with_seal(
    manifest: PairedPredictionManifest,
    *,
    seal: PredictionSeal,
) -> PairedPredictionManifest:
    seals = [*manifest.seals, seal]
    observed: dict[str, set[str]] = {}
    for row in seals:
        observed.setdefault(row.case_id, set()).add(row.variant_id)
    paired_case_ids = sorted(
        case_id
        for case_id, variants in observed.items()
        if variants == set(manifest.expected_variant_ids)
    )
    return PairedPredictionManifest.model_validate(
        {
            **manifest.model_dump(mode="json"),
            "seals": seals,
            "paired_case_ids": paired_case_ids,
            "all_predictions_sealed": set(paired_case_ids)
            == set(manifest.expected_case_ids),
        }
    )


def _reconcile_immutable_seal_state(
    manifest: PairedPredictionManifest,
    *,
    seal_path: Path,
    case_id: str,
    variant_id: str,
) -> tuple[PredictionSeal | None, bool]:
    manifest_seal = next(
        (
            seal
            for seal in manifest.seals
            if seal.case_id == case_id and seal.variant_id == variant_id
        ),
        None,
    )
    disk_seal = (
        PredictionSeal.model_validate(read_json(seal_path))
        if seal_path.is_file()
        else None
    )
    if manifest_seal is not None and disk_seal is None:
        raise ValueError("prediction progress references a missing immutable seal")
    if (
        manifest_seal is not None
        and disk_seal is not None
        and manifest_seal != disk_seal
    ):
        raise ValueError("prediction progress and immutable seal differ")
    observed = disk_seal or manifest_seal
    if observed is not None and (
        observed.case_id != case_id or observed.variant_id != variant_id
    ):
        raise ValueError("orphan prediction seal identity drifted")
    return observed, manifest_seal is None and disk_seal is not None


def _path_reference(root: Path, path: Path) -> QualityArtifactReference:
    path = path.resolve()
    try:
        relative = path.relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError("quality runtime artifact escapes the project root") from exc
    return QualityArtifactReference(
        artifact_path=relative,
        sha256=file_sha256(path),
    )
