"""Resumable paired shadow runs for legacy and retrieval-first runtime variants."""

from __future__ import annotations

import csv
import io
import json
import math
import random
import re
import time
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.memory_context import DailyMemoryContext
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    ContextManifest,
    DailyAnalysis,
)
from news_scalping_lab.contracts.runtime_retrieval import (
    RuntimeEvidenceMemo,
    RuntimeEvidenceMemoPack,
    RuntimeEvidencePackManifest,
    RuntimeEvidencePackPlan,
    RuntimeRetrievalTrace,
)
from news_scalping_lab.evaluation.quality_observations import (
    RetrievalCaseObservation,
    RetrievalClusterObservation,
)
from news_scalping_lab.evaluation.shadow import SHADOW_DAILY_P95_BUDGET_MS
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.memory.index import active_memory_snapshot_manifest
from news_scalping_lab.retrieval.production_embedding import (
    create_configured_embedding_provider,
)
from news_scalping_lab.retrieval.store import LocalRetrievalStore
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    now_kst,
    parse_datetime,
    read_json,
    sha256_bytes,
    sha256_text,
    write_json,
)

RUNTIME_VARIANT_SHADOW_VERSION = "nslab.runtime_variant_shadow.v2"
RUNTIME_VARIANT_SHADOW_ROOT = Path("runs/semantic_brain_upgrade/runtime_variant_shadow")
RuntimeVariantId = Literal["V0", "V1"]
_RUNTIME_VARIANTS: tuple[RuntimeVariantId, ...] = ("V0", "V1")
QUALITY_HIGH20_PROBABILITY_POLICY_VERSION = (
    "nslab.confidence_label_unconditional_high20_probability.v1"
)
QUALITY_MARKET_UNIVERSE_POLICY_VERSION = (
    "nslab.d1_intersection_raw_outcome_eligible_labels.v1"
)
QUALITY_BRIER_POPULATION_POLICY_VERSION = (
    "nslab.brier_excludes_outcome_ineligible_rows.v1"
)
QUALITY_HIGH20_CONFIDENCE_PROBABILITIES = {
    "very_high": 0.9,
    "high": 0.75,
    "medium": 0.5,
    "low": 0.25,
    "speculative": 0.1,
}
_OUTCOME_TICKER_PATTERN = re.compile(r"^[0-9]{6}$")
_MARKET_METRIC_KEYS = (
    "recall_at_20",
    "precision_at_20",
    "mrr",
    "brier",
    "final_memory_citation_rate",
    "upper_limit_recall_at_5",
    "upper_limit_recall_at_10",
    "upper_limit_recall_at_20",
    "high20_recall_at_5",
    "high20_recall_at_10",
    "high20_recall_at_20",
    "high10_recall_at_5",
    "high10_recall_at_10",
    "high10_recall_at_20",
    "high20_precision_at_5",
    "high20_precision_at_10",
    "high20_precision_at_20",
    "leader_upper_limit_hit",
    "leader_high20_hit",
    "calibration_absolute_error",
)


@dataclass(frozen=True)
class RuntimeVariantShadowResult:
    report: dict[str, Any]
    report_path: Path
    progress_path: Path


@dataclass(frozen=True)
class CanonicalOutcomeUniverse:
    trade_date: date
    all_rows_by_ticker: dict[str, dict[str, Any]]
    rows_by_ticker: dict[str, dict[str, Any]]
    excluded_tickers: tuple[str, ...]
    upper_limit_tickers: frozenset[str]
    high20_tickers: frozenset[str]
    high10_tickers: frozenset[str]
    leader_ticker: str
    max_return_tickers: frozenset[str]
    universe_root_sha256: str

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self.rows_by_ticker))

    @property
    def raw_tickers(self) -> tuple[str, ...]:
        return tuple(sorted(self.all_rows_by_ticker))


async def run_runtime_variant_shadow(
    root: Path,
    *,
    settings: Settings,
    selection_path: Path,
    split: Literal["CALIBRATION", "HOLDOUT"] = "CALIBRATION",
    case_limit: int | None = None,
) -> RuntimeVariantShadowResult:
    """Run paired variants with identical news/truth and persist after every call."""

    root = root.resolve()
    selection_path = selection_path.resolve()
    selection, selection_sha256 = _read_selection_with_sha256(selection_path)
    split_cases = [item for item in selection["cases"] if item.get("split") == split]
    cases = list(split_cases)
    if case_limit is not None:
        if case_limit < 1:
            raise ValueError("shadow case limit must be positive")
        cases = cases[:case_limit]
    if not cases:
        raise ValueError(f"semantic upgrade split has no {split} cases")
    brain_path = root / "brain" / "current" / "brain_manifest.json"
    brain, brain_sha256 = _read_json_file_once(brain_path)
    if not isinstance(brain, dict) or brain.get("build_mode") != "llm-full":
        raise ValueError("runtime variant shadow requires an llm-full evaluation brain")
    snapshot_id = brain.get("production_memory_snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("evaluation brain is missing its BUILD memory snapshot")
    coverage, _coverage_sha256 = _read_json_file_once(
        root / "brain" / "current" / "coverage_manifest.json"
    )
    if not isinstance(coverage, dict) or coverage.get("coverage_scope") != "EVALUATION_REPLAY_BUILD":
        raise ValueError("runtime variant shadow requires evaluation BUILD coverage")
    active_snapshot = active_memory_snapshot_manifest(root)
    if active_snapshot is None or active_snapshot.snapshot_id != snapshot_id or not active_snapshot.evaluation_only:
        raise ValueError("runtime variant shadow requires an evaluation-only snapshot")
    identity = {
        "schema_version": RUNTIME_VARIANT_SHADOW_VERSION,
        "selection_sha256": selection_sha256,
        "split": split,
        "evaluation_scope": ("FORMAL_SPLIT" if len(cases) == len(split_cases) else "SMOKE"),
        "formal_split_case_count": len(split_cases),
        "case_limit": case_limit,
        "case_ids": [str(item["episode_id"]) for item in cases],
        "brain_manifest_sha256": brain_sha256,
        "memory_snapshot_id": snapshot_id,
        "variants": ["V0", "V1"],
        "common_supporting_vector_search": "DISABLED_FORCE_EMPTY",
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm.model,
        "llm_reasoning_effort": settings.llm.reasoning_effort,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "evidence_policy": settings.evidence_policy.value,
        "web_provider": settings.web_provider,
    }
    run_id = (
        "RVSHADOW-"
        + sha256_text(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")))[:20].upper()
    )
    output_dir = root / RUNTIME_VARIANT_SHADOW_ROOT / run_id
    progress_path = output_dir / "progress.json"
    progress = _load_progress(progress_path, identity=identity, run_id=run_id)

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
        ),
        "V1": DailyAnalyzer(
            settings,
            llm=base_llm,
            retrieval=retrieval,
            embedding_provider=embedding_provider,
            runtime_retrieval_variant="v4",
        ),
    }
    sealed = {
        (str(row["case_id"]), str(row["variant_id"])): row
        for row in progress["prediction_seals"]
    }
    observed = {
        (str(row["case_id"]), str(row["variant_id"]))
        for row in progress["observations"]
    }
    case_news_sha256: dict[str, str] = {}

    # Phase 1 is outcome-blind across the complete selected run. No case may be
    # scored while a later case still lacks either variant seal.
    for case in cases:
        case_id = str(case["episode_id"])
        news_csv, news_sha256, cutoff_at = _materialize_case_news(
            root,
            output_dir=output_dir,
            case=case,
        )
        case_news_sha256[case_id] = news_sha256
        trade_date = parse_datetime(cutoff_at).date()
        preloaded_news_batch = load_news_csv(news_csv, trade_date=trade_date)
        if preloaded_news_batch.sha256 != news_sha256:
            raise ValueError("shadow materialized news failed its sealed hash")
        for variant_id in _RUNTIME_VARIANTS:
            if (case_id, variant_id) in sealed:
                continue
            runtime_before = _runtime_counter_snapshot(
                root,
                llm=base_llm,
                embedding_provider=embedding_provider,
            )
            started = time.perf_counter()
            analysis = await analyzers[variant_id].analyze(
                news_csv=news_csv,
                trade_date=trade_date,
                cutoff_at=parse_datetime(cutoff_at),
                mode="exhaustive",
                web_search=False,
                shadow_preloaded_news_batch=preloaded_news_batch,
            )
            elapsed = time.perf_counter() - started
            runtime_metrics = _runtime_counter_delta(
                root,
                before=runtime_before,
                llm=base_llm,
                embedding_provider=embedding_provider,
            )
            prediction_seal = _seal_shadow_prediction(
                root,
                analysis=analysis,
                case_id=case_id,
                variant_id=variant_id,
                news_sha256=news_sha256,
                elapsed_seconds=elapsed,
                runtime_metrics=runtime_metrics,
            )
            progress["prediction_seals"].append(prediction_seal)
            progress["completed_prediction_seal_count"] = len(
                progress["prediction_seals"]
            )
            write_json(progress_path, progress)
            sealed[(case_id, variant_id)] = prediction_seal

    expected_seal_keys = {
        (str(case["episode_id"]), variant_id)
        for case in cases
        for variant_id in _RUNTIME_VARIANTS
    }
    if set(sealed) != expected_seal_keys:
        raise ValueError("runtime shadow prediction seal closure is incomplete")

    # Validate every persisted prediction/context pair before the first outcome
    # path is even resolved. This same ordering is used after resume.
    for case in cases:
        case_id = str(case["episode_id"])
        news_sha256 = case_news_sha256[case_id]
        case_prediction_seals = [
            sealed[(case_id, variant_id)] for variant_id in _RUNTIME_VARIANTS
        ]
        _verify_paired_shadow_prediction_closure(
            root,
            case=case,
            prediction_seals=case_prediction_seals,
            expected_news_sha256=news_sha256,
            memory_snapshot_id=snapshot_id,
        )

    # Phase 2 may now open each case outcome once and feed the same verified
    # bytes to both variants.
    for case in cases:
        case_id = str(case["episode_id"])
        news_sha256 = case_news_sha256[case_id]
        case_prediction_seals = [
            sealed[(case_id, variant_id)] for variant_id in _RUNTIME_VARIANTS
        ]
        if {(case_id, variant_id) for variant_id in _RUNTIME_VARIANTS} <= observed:
            expected_truth_sha256 = _reference_sha256(case.get("outcome_ledger"))
            case_observations = [
                row
                for row in progress["observations"]
                if str(row.get("case_id")) == case_id
            ]
            if any(
                row.get("truth_sha256") != expected_truth_sha256
                for row in case_observations
            ):
                raise ValueError("runtime shadow resumed observation truth drifted")
            continue
        case_observations = _score_paired_shadow_case(
            root,
            case=case,
            prediction_seals=case_prediction_seals,
            expected_news_sha256=news_sha256,
            memory_snapshot_id=snapshot_id,
        )
        progress["observations"].extend(case_observations)
        progress["completed_observation_count"] = len(progress["observations"])
        write_json(progress_path, progress)
        observed.update(
            (str(row["case_id"]), str(row["variant_id"]))
            for row in case_observations
        )

    report = _build_report(progress, expected_case_count=len(cases))
    report_path = output_dir / "runtime_variant_shadow_report.json"
    write_json(report_path, report)
    (output_dir / "runtime_variant_shadow_report.md").write_text(
        _render_report(report),
        encoding="utf-8",
    )
    return RuntimeVariantShadowResult(
        report=report,
        report_path=report_path,
        progress_path=progress_path,
    )


def _read_json_file_once(path: Path) -> tuple[object, str]:
    if not path.is_file():
        raise ValueError(f"shadow source artifact is missing: {path}")
    payload_bytes = path.read_bytes()
    try:
        payload = json.loads(payload_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"shadow source artifact is invalid JSON: {path}") from exc
    return payload, sha256_bytes(payload_bytes)


def _read_selection_with_sha256(path: Path) -> tuple[dict[str, Any], str]:
    payload, payload_sha256 = _read_json_file_once(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "nslab.semantic_upgrade_split_selection.v1"
        or not isinstance(payload.get("cases"), list)
    ):
        raise ValueError("semantic upgrade selection artifact is invalid")
    return payload, payload_sha256


def _read_selection(path: Path) -> dict[str, Any]:
    return _read_selection_with_sha256(path)[0]


def _load_progress(
    path: Path,
    *,
    identity: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": RUNTIME_VARIANT_SHADOW_VERSION,
            "run_id": run_id,
            "identity": identity,
            "prediction_seals": [],
            "completed_prediction_seal_count": 0,
            "observations": [],
            "completed_observation_count": 0,
            "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
        }
    payload = read_json(path)
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != RUNTIME_VARIANT_SHADOW_VERSION
        or payload.get("run_id") != run_id
        or payload.get("identity") != identity
    ):
        raise ValueError("existing runtime shadow progress has a different identity")
    observations = payload.get("observations")
    prediction_seals = payload.get("prediction_seals")
    if not isinstance(observations, list) or not isinstance(prediction_seals, list):
        raise ValueError("runtime shadow progress observations are invalid")
    _validate_shadow_progress_closure(payload)
    return payload


def _validate_shadow_progress_closure(progress: dict[str, Any]) -> None:
    identity = progress.get("identity")
    if not isinstance(identity, dict) or not isinstance(identity.get("case_ids"), list):
        raise ValueError("runtime shadow progress identity is invalid")
    expected_cases = {str(value) for value in identity["case_ids"]}
    expected_variants = {"V0", "V1"}

    def keyed_rows(field: str) -> dict[tuple[str, str], dict[str, Any]]:
        raw_rows = progress.get(field)
        if not isinstance(raw_rows, list) or not all(
            isinstance(row, dict) for row in raw_rows
        ):
            raise ValueError(f"runtime shadow progress {field} is invalid")
        rows = [row for row in raw_rows if isinstance(row, dict)]
        keyed = {
            (str(row.get("case_id")), str(row.get("variant_id"))): row
            for row in rows
        }
        if len(keyed) != len(rows):
            raise ValueError(f"runtime shadow progress {field} is duplicated")
        if any(
            case_id not in expected_cases or variant_id not in expected_variants
            for case_id, variant_id in keyed
        ):
            raise ValueError(f"runtime shadow progress {field} escaped its identity")
        return keyed

    seals = keyed_rows("prediction_seals")
    observations = keyed_rows("observations")
    expected_seal_keys = {
        (case_id, variant_id)
        for case_id in expected_cases
        for variant_id in expected_variants
    }
    if observations and set(seals) != expected_seal_keys:
        raise ValueError(
            "runtime shadow scored progress predates global prediction seal closure"
        )
    for (case_id, variant_id), seal in seals.items():
        if (
            seal.get("schema_version")
            != "nslab.runtime_variant_prediction_seal.v1"
            or seal.get("case_id") != case_id
            or seal.get("variant_id") != variant_id
            or seal.get("outcome_reference_count") != 0
        ):
            raise ValueError("runtime shadow progress prediction seal is invalid")
    if not set(observations).issubset(seals):
        raise ValueError("runtime shadow observation has no prediction seal")
    for key, observation in observations.items():
        seal = seals[key]
        prediction_reference = seal.get("prediction")
        context_reference = seal.get("context_manifest")
        if (
            not isinstance(prediction_reference, dict)
            or not isinstance(context_reference, dict)
            or observation.get("news_sha256") != seal.get("news_sha256")
            or observation.get("prediction_sha256")
            != prediction_reference.get("sha256")
            or observation.get("context_manifest_sha256")
            != context_reference.get("sha256")
        ):
            raise ValueError("runtime shadow observation is not bound to its seal")
    for case_id in expected_cases:
        observed_variants = {
            variant_id
            for observed_case, variant_id in observations
            if observed_case == case_id
        }
        if observed_variants and observed_variants != expected_variants:
            raise ValueError("runtime shadow outcome observations are not paired")
        if observed_variants:
            case_observations = [
                row
                for (observed_case, _variant_id), row in observations.items()
                if observed_case == case_id
            ]
            if len({row.get("truth_sha256") for row in case_observations}) != 1:
                raise ValueError("runtime shadow paired observations used different truth")
    if progress.get("completed_prediction_seal_count") != len(seals):
        raise ValueError("runtime shadow prediction seal count is stale")
    if progress.get("completed_observation_count") != len(observations):
        raise ValueError("runtime shadow observation count is stale")


def _materialize_case_news(
    root: Path,
    *,
    output_dir: Path,
    case: dict[str, Any],
) -> tuple[Path, str, str]:
    case_id = str(case["episode_id"])
    index_path = _artifact_path(root, case["normalized_index"])
    source_path = _artifact_path(root, case["source_ledger"])
    index_bytes = _read_verified_reference_bytes(
        case["normalized_index"],
        index_path,
    )
    source_bytes = _read_verified_reference_bytes(
        case["source_ledger"],
        source_path,
    )
    index = _decode_json_bytes(index_bytes, label="shadow normalized index")
    if not isinstance(index, dict) or not isinstance(index.get("cutoff_at"), str):
        raise ValueError(f"shadow case index is invalid: {case_id}")
    cutoff_at = str(index["cutoff_at"])
    rows: list[dict[str, str]] = []
    try:
        source_lines = source_bytes.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("shadow source ledger is not valid UTF-8") from exc
    for line_number, line in enumerate(source_lines, start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"source ledger row {line_number} is invalid JSON"
            ) from exc
        if not isinstance(item, dict):
            raise ValueError("source ledger row must be an object")
        if item.get("available_before_cutoff") is not True:
            continue
        published = str(item.get("published_at_kst", ""))
        if "T" not in published:
            raise ValueError("source ledger row has no KST publication time")
        row_date, row_time = published.split("T", 1)
        rows.append(
            {
                "date": row_date,
                "time": row_time,
                "title": str(item.get("title", "")),
                "body": str(item.get("body", "")),
            }
        )
    if not rows:
        raise ValueError(f"shadow case has no cutoff-safe news: {case_id}")
    csv_buffer = io.StringIO(newline="")
    writer = csv.DictWriter(
        csv_buffer,
        fieldnames=["date", "time", "title", "body"],
    )
    writer.writeheader()
    writer.writerows(rows)
    csv_bytes = csv_buffer.getvalue().encode("utf-8-sig")
    news_sha256 = sha256_bytes(csv_bytes)
    csv_path = output_dir / "inputs" / f"{case_id}.csv"
    _write_immutable_shadow_bytes(csv_path, csv_bytes)
    receipt_path = csv_path.with_suffix(".receipt.json")
    receipt = {
        "schema_version": "nslab.shadow_news_reconstruction.v1",
        "episode_id": case_id,
        "source_ledger_sha256": sha256_bytes(source_bytes),
        "normalized_index_sha256": sha256_bytes(index_bytes),
        "news_csv_sha256": news_sha256,
        "row_count": len(rows),
        "cutoff_at": cutoff_at,
    }
    _write_immutable_shadow_bytes(
        receipt_path,
        (canonical_json(receipt) + "\n").encode("utf-8"),
    )
    return csv_path, news_sha256, cutoff_at


def _artifact_path(root: Path, reference: Any) -> Path:
    if not isinstance(reference, dict) or not isinstance(reference.get("artifact_path"), str):
        raise ValueError("shadow artifact reference is invalid")
    path = Path(str(reference["artifact_path"]))
    return (path if path.is_absolute() else root / path).resolve()


def _reference_sha256(reference: Any) -> str:
    if not isinstance(reference, dict):
        raise ValueError("shadow artifact reference is invalid")
    expected_sha256 = reference.get("sha256")
    if (
        not isinstance(expected_sha256, str)
        or len(expected_sha256) != 64
        or any(character not in "0123456789abcdef" for character in expected_sha256)
    ):
        raise ValueError("shadow artifact reference has no valid SHA-256")
    return expected_sha256


def _read_verified_reference_bytes(reference: Any, path: Path) -> bytes:
    expected_sha256 = _reference_sha256(reference)
    try:
        payload_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"shadow source artifact is missing: {path}") from exc
    if sha256_bytes(payload_bytes) != expected_sha256:
        raise ValueError(f"shadow source artifact failed hash verification: {path}")
    return payload_bytes


def _decode_json_bytes(payload_bytes: bytes, *, label: str) -> object:
    try:
        return json.loads(payload_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc


def _write_immutable_shadow_bytes(path: Path, payload_bytes: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(payload_bytes)
        return
    except FileExistsError:
        pass
    try:
        existing_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"shadow immutable artifact is unreadable: {path}") from exc
    if existing_bytes != payload_bytes:
        raise ValueError(f"shadow immutable artifact drifted: {path}")


def _shadow_output_artifact_path(root: Path, reference: Any) -> Path:
    resolved_root = root.resolve()
    path = _artifact_path(resolved_root, reference)
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("shadow sealed artifact escaped the evaluation root") from exc
    return path


def _seal_shadow_prediction(
    root: Path,
    *,
    analysis: DailyAnalysis,
    case_id: str,
    variant_id: RuntimeVariantId,
    news_sha256: str,
    elapsed_seconds: float,
    runtime_metrics: dict[str, Any],
) -> dict[str, Any]:
    manifest = analysis.context_manifest
    prediction = analysis.blind_prediction
    expected_runtime_variant = "legacy" if variant_id == "V0" else "v4"
    if (
        analysis.run_id != manifest.run_id
        or analysis.trade_date != manifest.trade_date
        or analysis.trade_date != prediction.trade_date
        or as_kst(analysis.cutoff_at) != as_kst(manifest.cutoff_at)
        or as_kst(analysis.cutoff_at) != as_kst(prediction.cutoff_at)
    ):
        raise ValueError("runtime shadow analysis identity is inconsistent")
    if (
        manifest.news_sha256 != news_sha256
        or manifest.llm_model_config.get("runtime_retrieval_variant")
        != expected_runtime_variant
    ):
        raise ValueError("runtime shadow analysis input or variant identity drifted")
    if (
        manifest.blind_web_search_call_count
        or manifest.external_web_evidence_count
        or manifest.no_d_outcome_exposed is not True
    ):
        raise ValueError("runtime variant shadow violated the BLIND boundary")
    if not isinstance(manifest.prediction_artifact, str) or not manifest.prediction_artifact:
        raise ValueError("runtime variant shadow prediction artifact is missing")
    if not isinstance(manifest.prediction_sha256, str):
        raise ValueError("runtime variant shadow prediction hash is missing")

    context_path = (
        root.resolve() / "runs" / "manifests" / f"{manifest.run_id}.json"
    )
    context_payload, context_sha256 = _read_json_file_once(context_path)
    persisted_manifest = ContextManifest.model_validate(context_payload)
    if persisted_manifest.model_dump(mode="json") != manifest.model_dump(mode="json"):
        raise ValueError("runtime shadow persisted context manifest drifted")

    prediction_reference = {
        "artifact_path": manifest.prediction_artifact,
        "sha256": manifest.prediction_sha256,
    }
    prediction_path = _shadow_output_artifact_path(root, prediction_reference)
    prediction_bytes = _read_verified_reference_bytes(
        prediction_reference,
        prediction_path,
    )
    persisted_prediction = BlindPrediction.model_validate(
        _decode_json_bytes(
            prediction_bytes,
            label="shadow prediction artifact",
        )
    )
    if persisted_prediction.model_dump(mode="json") != prediction.model_dump(
        mode="json"
    ):
        raise ValueError("runtime shadow persisted prediction drifted")
    if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0.0:
        raise ValueError("runtime shadow elapsed time is invalid")

    resolved_root = root.resolve()
    return {
        "schema_version": "nslab.runtime_variant_prediction_seal.v1",
        "case_id": case_id,
        "variant_id": variant_id,
        "runtime_retrieval_variant": expected_runtime_variant,
        "trade_date": analysis.trade_date.isoformat(),
        "cutoff_at": as_kst(analysis.cutoff_at).isoformat(),
        "run_id": manifest.run_id,
        "news_sha256": news_sha256,
        "prediction": {
            "artifact_path": prediction_path.relative_to(resolved_root).as_posix(),
            "sha256": sha256_bytes(prediction_bytes),
        },
        "context_manifest": {
            "artifact_path": context_path.relative_to(resolved_root).as_posix(),
            "sha256": context_sha256,
        },
        "elapsed_seconds": elapsed_seconds,
        "runtime_metrics": runtime_metrics,
        "sealed_at": now_kst().isoformat(),
        "outcome_reference_count": 0,
    }


def _paired_shadow_prediction_seals(
    prediction_seals: Sequence[dict[str, Any]],
    *,
    case_id: str,
    expected_news_sha256: str,
) -> dict[RuntimeVariantId, dict[str, Any]]:
    if len(prediction_seals) != len(_RUNTIME_VARIANTS):
        raise ValueError("runtime shadow outcome access requires a paired prediction seal")
    paired: dict[RuntimeVariantId, dict[str, Any]] = {}
    for seal in prediction_seals:
        variant_value = seal.get("variant_id")
        if variant_value not in _RUNTIME_VARIANTS:
            raise ValueError("runtime shadow prediction seal variant is invalid")
        variant_id: RuntimeVariantId = variant_value
        if variant_id in paired:
            raise ValueError("runtime shadow prediction seals are duplicated")
        if (
            seal.get("schema_version")
            != "nslab.runtime_variant_prediction_seal.v1"
            or seal.get("case_id") != case_id
            or seal.get("news_sha256") != expected_news_sha256
            or seal.get("outcome_reference_count") != 0
        ):
            raise ValueError("runtime shadow prediction seal identity is invalid")
        paired[variant_id] = seal
    if set(paired) != set(_RUNTIME_VARIANTS):
        raise ValueError("runtime shadow outcome access requires a paired prediction seal")
    return paired


def _load_shadow_prediction_seal(
    root: Path,
    *,
    seal: dict[str, Any],
) -> tuple[BlindPrediction, ContextManifest, str, str]:
    prediction_reference = seal.get("prediction")
    context_reference = seal.get("context_manifest")
    if not isinstance(prediction_reference, dict) or not isinstance(
        context_reference,
        dict,
    ):
        raise ValueError("runtime shadow prediction seal references are invalid")
    prediction_path = _shadow_output_artifact_path(root, prediction_reference)
    context_path = _shadow_output_artifact_path(root, context_reference)
    prediction_bytes = _read_verified_reference_bytes(
        prediction_reference,
        prediction_path,
    )
    context_bytes = _read_verified_reference_bytes(
        context_reference,
        context_path,
    )
    prediction = BlindPrediction.model_validate(
        _decode_json_bytes(prediction_bytes, label="shadow sealed prediction")
    )
    manifest = ContextManifest.model_validate(
        _decode_json_bytes(context_bytes, label="shadow sealed context manifest")
    )
    run_id = seal.get("run_id")
    if (
        not isinstance(run_id, str)
        or manifest.run_id != run_id
        or prediction.trade_date != manifest.trade_date
        or as_kst(prediction.cutoff_at) != as_kst(manifest.cutoff_at)
        or manifest.news_sha256 != seal.get("news_sha256")
        or manifest.prediction_artifact != prediction_reference.get("artifact_path")
        or manifest.prediction_sha256 != sha256_bytes(prediction_bytes)
        or context_path
        != root.resolve() / "runs" / "manifests" / f"{manifest.run_id}.json"
    ):
        raise ValueError("runtime shadow sealed prediction closure is invalid")
    return (
        prediction,
        manifest,
        sha256_bytes(prediction_bytes),
        sha256_bytes(context_bytes),
    )


def _score_paired_shadow_case(
    root: Path,
    *,
    case: dict[str, Any],
    prediction_seals: Sequence[dict[str, Any]],
    expected_news_sha256: str,
    memory_snapshot_id: str,
) -> list[dict[str, Any]]:
    paired, loaded = _verify_paired_shadow_prediction_closure(
        root,
        case=case,
        prediction_seals=prediction_seals,
        expected_news_sha256=expected_news_sha256,
        memory_snapshot_id=memory_snapshot_id,
    )

    # Outcome resolution is deliberately below the complete pair and artifact closure.
    truth_reference = case.get("outcome_ledger")
    truth_path = _artifact_path(root, truth_reference)
    truth_bytes = _read_verified_reference_bytes(truth_reference, truth_path)
    truth_sha256 = sha256_bytes(truth_bytes)
    observations: list[dict[str, Any]] = []
    for variant_id in _RUNTIME_VARIANTS:
        (
            prediction,
            manifest,
            prediction_sha256,
            context_sha256,
            trace_stats,
        ) = loaded[variant_id]
        seal = paired[variant_id]
        elapsed_seconds = seal.get("elapsed_seconds")
        runtime_metrics = seal.get("runtime_metrics")
        if (
            not isinstance(elapsed_seconds, (int, float))
            or isinstance(elapsed_seconds, bool)
            or not math.isfinite(float(elapsed_seconds))
            or float(elapsed_seconds) < 0.0
            or not isinstance(runtime_metrics, dict)
        ):
            raise ValueError("runtime shadow prediction seal metrics are invalid")
        observations.append(
            _observation(
                case=case,
                variant_id=variant_id,
                news_sha256=expected_news_sha256,
                truth_bytes=truth_bytes,
                truth_sha256=truth_sha256,
                prediction=prediction,
                manifest=manifest,
                prediction_sha256=prediction_sha256,
                context_manifest_sha256=context_sha256,
                elapsed_seconds=float(elapsed_seconds),
                runtime_metrics=runtime_metrics,
                trace_stats=trace_stats,
            )
        )
    return observations


def _verify_paired_shadow_prediction_closure(
    root: Path,
    *,
    case: dict[str, Any],
    prediction_seals: Sequence[dict[str, Any]],
    expected_news_sha256: str,
    memory_snapshot_id: str,
) -> tuple[
    dict[RuntimeVariantId, dict[str, Any]],
    dict[
        RuntimeVariantId,
        tuple[BlindPrediction, ContextManifest, str, str, dict[str, Any]],
    ],
]:
    case_id = str(case["episode_id"])
    paired = _paired_shadow_prediction_seals(
        prediction_seals,
        case_id=case_id,
        expected_news_sha256=expected_news_sha256,
    )
    loaded: dict[
        RuntimeVariantId,
        tuple[BlindPrediction, ContextManifest, str, str, dict[str, Any]],
    ] = {}
    case_trade_date = date.fromisoformat(str(case["trade_date"]))
    for variant_id in _RUNTIME_VARIANTS:
        seal = paired[variant_id]
        prediction, manifest, prediction_sha256, context_sha256 = (
            _load_shadow_prediction_seal(root, seal=seal)
        )
        expected_runtime_variant = "legacy" if variant_id == "V0" else "v4"
        seal_cutoff = seal.get("cutoff_at")
        if (
            prediction.trade_date != case_trade_date
            or str(seal.get("trade_date")) != case_trade_date.isoformat()
            or not isinstance(seal_cutoff, str)
            or as_kst(parse_datetime(seal_cutoff)) != as_kst(manifest.cutoff_at)
            or prediction.context_manifest_id != manifest.run_id
            or manifest.llm_model_config.get("runtime_retrieval_variant")
            != expected_runtime_variant
            or manifest.blind_price_repository_access_count != 0
            or manifest.blind_current_price_access_count != 0
            or manifest.blind_web_search_call_count != 0
            or manifest.external_web_evidence_count != 0
            or manifest.no_d_outcome_exposed is not True
        ):
            raise ValueError("runtime shadow paired prediction case identity drifted")
        trace_stats = _trace_stats(root, manifest)
        if trace_stats["memory_snapshot_id"] != memory_snapshot_id:
            raise ValueError("runtime variant shadow used the wrong BUILD snapshot")
        loaded[variant_id] = (
            prediction,
            manifest,
            prediction_sha256,
            context_sha256,
            trace_stats,
        )
    parity_fields = (
        "news_sha256",
        "event_clustering_result_sha256",
        "open_world_first_analysis_sha256",
        "news_novelty_review_sha256",
    )
    manifests = [loaded[variant_id][1] for variant_id in _RUNTIME_VARIANTS]
    if any(
        getattr(manifests[0], field_name) != getattr(manifests[1], field_name)
        for field_name in parity_fields
    ):
        raise ValueError("runtime shadow paired pre-retrieval identity drifted")
    return paired, loaded


def _observation(
    *,
    case: dict[str, Any],
    variant_id: RuntimeVariantId,
    news_sha256: str,
    truth_bytes: bytes,
    truth_sha256: str,
    prediction: BlindPrediction,
    manifest: ContextManifest,
    prediction_sha256: str,
    context_manifest_sha256: str,
    elapsed_seconds: float,
    runtime_metrics: dict[str, Any],
    trace_stats: dict[str, Any],
) -> dict[str, Any]:
    if manifest.blind_web_search_call_count or manifest.external_web_evidence_count:
        raise ValueError("runtime variant shadow attempted BLIND web access")
    metrics = _prediction_metrics(prediction, None, truth_bytes=truth_bytes)
    general_citation_rate = _final_memory_citation_rate(prediction)
    runtime_citation_rate = _runtime_final_candidate_citation_rate(
        prediction,
        trace_stats=trace_stats,
    )
    metrics["general_memory_citation_rate"] = general_citation_rate
    metrics["final_memory_citation_rate"] = runtime_citation_rate if variant_id == "V1" else general_citation_rate
    return {
        "case_id": str(case["episode_id"]),
        "trade_date": str(case["trade_date"]),
        "split": str(case["split"]),
        "variant_id": variant_id,
        "runtime_retrieval_variant": ("legacy" if variant_id == "V0" else "v4"),
        "news_sha256": news_sha256,
        "truth_sha256": truth_sha256,
        "prediction_sha256": prediction_sha256,
        "context_manifest_sha256": context_manifest_sha256,
        "pre_retrieval_identity": {
            "event_clustering_result_sha256": (manifest.event_clustering_result_sha256),
            "open_world_first_analysis_sha256": (manifest.open_world_first_analysis_sha256),
            "news_novelty_review_sha256": manifest.news_novelty_review_sha256,
            "open_world_prompt_sha256": manifest.prompt_hashes.get("open_world_first_analysis"),
            "news_novelty_prompt_sha256": manifest.prompt_hashes.get("news_novelty_review"),
        },
        "run_id": manifest.run_id,
        "memory_snapshot_id": trace_stats["memory_snapshot_id"],
        "blind_web_call_count": manifest.blind_web_search_call_count,
        "future_record_count": trace_stats["future_record_count"],
        "online_full_scan_count": trace_stats["online_full_scan_count"],
        "adaptive_trace_count": trace_stats["adaptive_trace_count"],
        "runtime_trace_count": trace_stats["runtime_trace_count"],
        "selected_record_count": trace_stats["selected_record_count"],
        "offline_unexposed_recovered_count": trace_stats["offline_unexposed_recovered_count"],
        "offline_unexposed_candidate_count": trace_stats["offline_unexposed_candidate_count"],
        "offline_unexposed_llm_exposed_count": trace_stats["offline_unexposed_llm_exposed_count"],
        "offline_unexposed_final_cited_count": trace_stats["offline_unexposed_final_cited_count"],
        "rare_mechanism_recovered_count": trace_stats["rare_mechanism_recovered_count"],
        "final_cited_record_count": trace_stats["final_cited_record_count"],
        "runtime_final_cited_candidate_count": len(trace_stats["runtime_final_candidate_ids"]),
        "selected_independent_unit_count": len(trace_stats["selected_independent_unit_ids"]),
        "selected_year_counts": dict(trace_stats["selected_year_counts"]),
        "issuer_day_duplicate_rate": (
            1.0 - len(trace_stats["selected_independent_unit_ids"]) / trace_stats["selected_record_count"]
            if trace_stats["selected_record_count"]
            else 0.0
        ),
        "lane_selected_counts": trace_stats["lane_selected_counts"],
        "elapsed_seconds": elapsed_seconds,
        "prompt_token_count": sum(manifest.token_counts.values()),
        "runtime_metrics": runtime_metrics,
        "metrics": metrics,
    }


def _runtime_counter_snapshot(
    root: Path,
    *,
    llm: Any,
    embedding_provider: Any,
) -> dict[str, Any]:
    identity_reader = getattr(llm, "identity", None)
    identity = identity_reader() if callable(identity_reader) else {}
    if not isinstance(identity, dict):
        identity = {}
    trace_dir = root / "runs" / "traces"
    return {
        "oauth_live_agent_call_count": int(identity.get("live_agent_call_count") or 0),
        "oauth_cache_event_count": int(identity.get("cache_hit_count") or 0),
        "embedding_query_count": int(getattr(embedding_provider, "embedding_query_count", 0) or 0),
        "embedding_text_count": int(getattr(embedding_provider, "embedding_text_count", 0) or 0),
        "embedding_input_char_count": int(getattr(embedding_provider, "embedding_input_char_count", 0) or 0),
        "trace_files": {path.resolve().as_posix() for path in trace_dir.glob("*.json") if path.is_file()},
        "process_memory": _process_memory_snapshot(),
        "captured_at": now_kst().isoformat(),
    }


def _runtime_counter_delta(
    root: Path,
    *,
    before: dict[str, Any],
    llm: Any,
    embedding_provider: Any,
) -> dict[str, Any]:
    after = _runtime_counter_snapshot(
        root,
        llm=llm,
        embedding_provider=embedding_provider,
    )
    new_trace_paths = sorted(set(after["trace_files"]) - set(before["trace_files"]))
    trace_counts: Counter[str] = Counter()
    prompt_tokens = 0
    completion_tokens = 0
    retry_count = 0
    trace_started_at = []
    for path_text in new_trace_paths:
        payload = read_json(Path(path_text))
        if not isinstance(payload, dict):
            continue
        operation = str(payload.get("operation") or "unknown")
        status = str(payload.get("status") or "unknown")
        started_at = payload.get("started_at")
        if isinstance(started_at, str):
            trace_started_at.append(parse_datetime(started_at))
        trace_counts[f"operation:{operation}"] += 1
        trace_counts[f"status:{status}"] += 1
        token_usage = payload.get("token_usage")
        if isinstance(token_usage, dict):
            prompt_tokens += int(token_usage.get("prompt_tokens_estimate") or 0)
            completion_tokens += int(token_usage.get("completion_tokens_estimate") or 0)
        retry_count += int(payload.get("retries") or 0)
    before_memory = before.get("process_memory")
    after_memory = after.get("process_memory")
    captured_at = before.get("captured_at")
    pre_llm_latency_seconds = None
    if isinstance(captured_at, str) and trace_started_at:
        pre_llm_latency_seconds = max(
            0.0,
            (min(trace_started_at) - parse_datetime(captured_at)).total_seconds(),
        )
    return {
        "logical_llm_call_count": sum(
            count
            for key, count in trace_counts.items()
            if key in {"operation:generate_text", "operation:generate_structured"}
        ),
        "oauth_live_agent_call_count": (
            int(after["oauth_live_agent_call_count"]) - int(before["oauth_live_agent_call_count"])
        ),
        "llm_checkpoint_hit_count": int(trace_counts["status:checkpoint_hit"]),
        "oauth_cache_event_count": (int(after["oauth_cache_event_count"]) - int(before["oauth_cache_event_count"])),
        "llm_prompt_tokens_estimate": prompt_tokens,
        "llm_completion_tokens_estimate": completion_tokens,
        "llm_retry_count": retry_count,
        "llm_error_trace_count": int(trace_counts["status:error"]),
        "embedding_query_count": (int(after["embedding_query_count"]) - int(before["embedding_query_count"])),
        "embedding_text_count": (int(after["embedding_text_count"]) - int(before["embedding_text_count"])),
        "embedding_input_char_count": (
            int(after["embedding_input_char_count"]) - int(before["embedding_input_char_count"])
        ),
        "new_llm_trace_count": len(new_trace_paths),
        "process_rss_before_bytes": _memory_value(before_memory, "rss_bytes"),
        "process_rss_after_bytes": _memory_value(after_memory, "rss_bytes"),
        "process_peak_working_set_bytes": _memory_value(
            after_memory,
            "peak_working_set_bytes",
        ),
        "peak_memory_scope": "PROCESS_LIFETIME" if after_memory else "UNAVAILABLE",
        "pre_llm_latency_seconds": pre_llm_latency_seconds,
        "pre_llm_latency_status": (
            "MEASURED_TO_FIRST_LLM_TRACE" if pre_llm_latency_seconds is not None else "UNAVAILABLE_NO_LLM_TRACE"
        ),
    }


def _process_memory_snapshot() -> dict[str, int] | None:
    try:
        import psutil  # type: ignore[import-untyped]
    except ImportError:
        return None
    memory = psutil.Process().memory_info()
    peak = getattr(memory, "peak_wset", None)
    return {
        "rss_bytes": int(memory.rss),
        "peak_working_set_bytes": int(peak) if isinstance(peak, int) else int(memory.rss),
    }


def _memory_value(value: Any, key: str) -> int | None:
    if not isinstance(value, dict):
        return None
    observed = value.get(key)
    return int(observed) if isinstance(observed, int) else None


def _trace_stats(
    root: Path,
    manifest: Any,
    *,
    expected_prediction_id: str | None = None,
    require_complete_provenance: bool = False,
) -> dict[str, Any]:
    """Verify finalized retrieval artifacts and retain every stage transition."""

    base: dict[str, Any] = {
        "adaptive_trace_count": 0,
        "runtime_trace_count": 0,
        "selected_record_count": 0,
        "selected_record_unique_count": 0,
        "searched_record_count": 0,
        "searched_record_unique_count": 0,
        "llm_exposed_record_count": 0,
        "memo_referenced_record_count": 0,
        "stock_cited_record_count": 0,
        "sector_cited_record_count": 0,
        "selected_unused_record_count": 0,
        "offline_unexposed_recovered_count": 0,
        "offline_unexposed_candidate_count": 0,
        "offline_unexposed_llm_exposed_count": 0,
        "offline_unexposed_final_cited_count": 0,
        "rare_mechanism_recovered_count": 0,
        "final_cited_record_count": 0,
        "final_cited_unique_record_count": 0,
        "future_record_count": 0,
        "blind_web_call_count": 0,
        "online_full_scan_count": 0,
        "lane_selected_counts": {},
        "lane_stage_counts": {},
        "runtime_final_candidate_ids": set(),
        "runtime_final_sector_ids": set(),
        "selected_independent_unit_ids": set(),
        "selected_episode_ids": set(),
        "unresolved_episode_record_ids": set(),
        "selected_year_counts": Counter(),
        "memory_snapshot_id": None,
        "snapshot_closure_verified": False,
        "artifact_closure_verified": False,
        "cluster_observations": [],
        "retrieval_observation": None,
        "evidence_pack_manifest_path": None,
        "evidence_pack_manifest_sha256": None,
        "evidence_assignment_count": 0,
        "evidence_unique_record_count": 0,
        "evidence_packed_call_count": 0,
        "evidence_provider_checkpoint_count": 0,
        "evidence_avoided_payload_occurrence_count": 0,
    }
    artifact = getattr(manifest, "daily_memory_context_artifact", None)
    if not artifact:
        if require_complete_provenance:
            raise ValueError("quality retrieval statistics require daily memory context")
        return base
    artifact_sha256 = getattr(manifest, "daily_memory_context_sha256", None)
    if not isinstance(artifact_sha256, str):
        raise ValueError("daily memory context hash is missing")
    _context_path, context_bytes = _read_verified_runtime_artifact(
        root,
        artifact_path=str(artifact),
        artifact_sha256=artifact_sha256,
        label="daily memory context",
    )
    context = DailyMemoryContext.model_validate(
        _decode_json_bytes(context_bytes, label="daily memory context")
    )
    if (
        context.run_id != manifest.run_id
        or context.trade_date != manifest.trade_date
        or as_kst(context.cutoff_at) != as_kst(manifest.cutoff_at)
    ):
        raise ValueError("daily memory context identity differs from its run manifest")
    base["memory_snapshot_id"] = context.memory_snapshot_id
    base["adaptive_trace_count"] = len(context.adaptive_retrieval_traces)
    for reference in (
        *context.population_manifests,
        *context.representative_set_manifests,
        *context.adaptive_retrieval_traces,
        *context.runtime_retrieval_traces,
        *context.runtime_evidence_traces,
        *context.runtime_evidence_memos,
    ):
        _verified_runtime_artifact(
            root,
            artifact_path=reference.artifact_path,
            artifact_sha256=reference.sha256,
            label="daily memory dependency",
        )
    if context.runtime_evidence_pack_manifest is not None:
        pack_reference = context.runtime_evidence_pack_manifest
        _pack_path, pack_bytes = _read_verified_runtime_artifact(
            root,
            artifact_path=pack_reference.artifact_path,
            artifact_sha256=pack_reference.sha256,
            label="runtime evidence pack manifest",
        )
        pack_manifest = RuntimeEvidencePackManifest.model_validate(
            _decode_json_bytes(
                pack_bytes,
                label="runtime evidence pack manifest",
            )
        )
        provider_checkpoint_count = _verify_runtime_evidence_pack_graph(
            root,
            manifest=pack_manifest,
        )
        if (
            pack_manifest.run_id != context.run_id
            or as_kst(pack_manifest.cutoff_at) != as_kst(context.cutoff_at)
            or pack_manifest.memory_snapshot_id
            != context.memory_snapshot_id
            or pack_manifest.cluster_ids
            != sorted(context.runtime_retrieval_cluster_ids)
            or context.runtime_evidence_assignment_count
            != pack_manifest.assignment_count
            or context.runtime_evidence_unique_record_count
            != pack_manifest.unique_record_count
            or context.runtime_evidence_packed_call_count
            != len(pack_manifest.packs)
            or context.runtime_evidence_avoided_payload_occurrence_count
            != pack_manifest.avoided_payload_occurrence_count
        ):
            raise ValueError("runtime evidence pack manifest differs from context")
        base.update(
            {
                "evidence_pack_manifest_path": pack_reference.artifact_path,
                "evidence_pack_manifest_sha256": pack_reference.sha256,
                "evidence_assignment_count": pack_manifest.assignment_count,
                "evidence_unique_record_count": pack_manifest.unique_record_count,
                "evidence_packed_call_count": len(pack_manifest.packs),
                "evidence_provider_checkpoint_count": provider_checkpoint_count,
                "evidence_avoided_payload_occurrence_count": (
                    pack_manifest.avoided_payload_occurrence_count
                ),
            }
        )
    trace_entries, final_manifest = _runtime_trace_entries(
        root,
        manifest=manifest,
        context=context,
        expected_prediction_id=expected_prediction_id,
    )
    base["runtime_trace_count"] = len(trace_entries)
    lanes: Counter[str] = Counter()
    lane_stages: dict[str, Counter[str]] = defaultdict(Counter)
    cluster_observations: list[RetrievalClusterObservation] = []
    all_record_ids: dict[str, set[str]] = defaultdict(set)
    occurrence_counts: Counter[str] = Counter()
    all_episode_ids: set[str] = set()
    all_independent_unit_ids: set[str] = set()
    all_year_counts: Counter[str] = Counter()
    for entry in trace_entries:
        trace_payload = entry.get("payload_bytes")
        if not isinstance(trace_payload, bytes):
            raise ValueError("runtime final trace has no verified payload")
        trace = RuntimeRetrievalTrace.model_validate(
            _decode_json_bytes(trace_payload, label="runtime final trace")
        )
        expected_cluster_id = str(entry["cluster_id"])
        if (
            trace.run_id != context.run_id
            or trace.cluster_id != expected_cluster_id
            or trace.memory_snapshot_id != context.memory_snapshot_id
            or as_kst(trace.cutoff_at) != as_kst(context.cutoff_at)
        ):
            raise ValueError("runtime final trace identity or snapshot differs")
        if trace.blind_web_call_count:
            raise ValueError("runtime final trace contains BLIND web calls")
        for reference in (
            *trace.source_population_manifests,
            *trace.source_representative_manifests,
        ):
            _verified_runtime_artifact(
                root,
                artifact_path=reference.artifact_path,
                artifact_sha256=reference.sha256,
                label="runtime trace dependency",
            )
        memo_ids_by_record = _runtime_memo_ids_by_record(
            root,
            trace=trace,
            require_complete=require_complete_provenance,
        )
        searched: set[str] = set()
        selected: set[str] = set()
        exposed: set[str] = set()
        memo_referenced: set[str] = set()
        stock_cited: set[str] = set()
        sector_cited: set[str] = set()
        offline_searched: set[str] = set()
        offline_selected: set[str] = set()
        offline_exposed: set[str] = set()
        offline_cited: set[str] = set()
        rare_selected: set[str] = set()
        independent_units: set[str] = set()
        episode_ids: set[str] = set()
        year_counts: Counter[str] = Counter()
        cluster_lane_stages: dict[str, Counter[str]] = defaultdict(Counter)
        for row in trace.rows:
            searched.add(row.record_id)
            lane = row.lane or "UNASSIGNED"
            cluster_lane_stages[lane]["SEARCHED"] += 1
            effective_available_from = row.replay_available_from or row.available_from
            is_future = (
                effective_available_from > manifest.cutoff_at
                or row.source_trade_date >= manifest.trade_date
            )
            base["future_record_count"] += int(is_future)
            if row.offline_payload_exposed is False:
                offline_searched.add(row.record_id)
            if "LANE_SELECTED" in row.stages:
                selected.add(row.record_id)
                independent_units.add(row.independent_unit_id)
                year_counts[str(row.source_trade_date.year)] += 1
                cluster_lane_stages[lane]["SELECTED"] += 1
                if row.offline_payload_exposed is False:
                    offline_selected.add(row.record_id)
                episode_id = _episode_id_from_record_id(row.record_id)
                if episode_id is None:
                    base["unresolved_episode_record_ids"].add(row.record_id)
                else:
                    episode_ids.add(episode_id)
            if row.runtime_payload_exposed:
                exposed.add(row.record_id)
                cluster_lane_stages[lane]["LLM_EXPOSED"] += 1
                if row.offline_payload_exposed is False:
                    offline_exposed.add(row.record_id)
            if row.evidence_memo_ids:
                memo_referenced.add(row.record_id)
                cluster_lane_stages[lane]["MEMO_REFERENCED"] += 1
                if set(row.evidence_memo_ids) != memo_ids_by_record.get(
                    row.record_id,
                    set(),
                ):
                    raise ValueError("runtime trace memo IDs differ from memo artifact")
            if row.final_candidate_ids:
                stock_cited.add(row.record_id)
                cluster_lane_stages[lane]["STOCK_CITED"] += 1
            if row.final_sector_ids:
                sector_cited.add(row.record_id)
                cluster_lane_stages[lane]["SECTOR_CITED"] += 1
            if row.final_candidate_ids or row.final_sector_ids:
                cluster_lane_stages[lane]["FINAL_CITED"] += 1
                if row.offline_payload_exposed is False:
                    offline_cited.add(row.record_id)
            base["runtime_final_candidate_ids"].update(row.final_candidate_ids)
            base["runtime_final_sector_ids"].update(row.final_sector_ids)
            if row.rare_payload and "LANE_SELECTED" in row.stages:
                rare_selected.add(row.record_id)
        final_cited = stock_cited | sector_cited
        selected_unused = selected - final_cited
        for _record_id in selected_unused:
            cluster_lane_stages[
                next(
                    (
                        row.lane or "UNASSIGNED"
                        for row in trace.rows
                        if row.record_id == _record_id
                    ),
                    "UNASSIGNED",
                )
            ]["SELECTED_UNUSED"] += 1
        if exposed != memo_referenced:
            raise ValueError("runtime LLM exposure does not close over evidence memos")
        if not final_cited.issubset(memo_referenced):
            raise ValueError("runtime final citations are not memo-backed")
        observed_offline = (
            len(offline_selected),
            len(offline_exposed),
            len(offline_cited),
            len(rare_selected),
        )
        declared_offline = (
            trace.offline_unexposed_recovered_count,
            trace.offline_unexposed_llm_exposed_count,
            trace.offline_unexposed_final_cited_count,
            trace.rare_mechanism_recovered_count,
        )
        if observed_offline != declared_offline:
            raise ValueError("runtime trace aggregate counters are stale")
        if entry.get("selected_record_count") not in (None, len(selected)):
            raise ValueError("runtime final manifest selected count is stale")
        if entry.get("final_cited_record_count") not in (None, len(final_cited)):
            raise ValueError("runtime final manifest citation count is stale")
        cluster = RetrievalClusterObservation(
            cluster_id=trace.cluster_id,
            trace_id=trace.trace_id,
            trace_artifact_path=str(entry["artifact_path"]),
            trace_sha256=str(entry["sha256"]),
            memory_snapshot_id=trace.memory_snapshot_id,
            evidence_memo_artifact_path=(
                trace.evidence_memo_artifact.artifact_path
                if trace.evidence_memo_artifact is not None
                else None
            ),
            evidence_memo_sha256=(
                trace.evidence_memo_artifact.sha256
                if trace.evidence_memo_artifact is not None
                else None
            ),
            searched_record_ids=sorted(searched),
            selected_record_ids=sorted(selected),
            llm_exposed_record_ids=sorted(exposed),
            memo_referenced_record_ids=sorted(memo_referenced),
            stock_cited_record_ids=sorted(stock_cited),
            sector_cited_record_ids=sorted(sector_cited),
            final_cited_record_ids=sorted(final_cited),
            selected_unused_record_ids=sorted(selected_unused),
            offline_unexposed_searched_record_ids=sorted(offline_searched),
            offline_unexposed_selected_record_ids=sorted(offline_selected),
            offline_unexposed_llm_exposed_record_ids=sorted(offline_exposed),
            offline_unexposed_final_cited_record_ids=sorted(offline_cited),
            rare_selected_record_ids=sorted(rare_selected),
            independent_unit_ids=sorted(independent_units),
            episode_ids=sorted(episode_ids),
            year_counts=dict(sorted(year_counts.items())),
            lane_stage_counts={
                lane: dict(sorted(stages.items()))
                for lane, stages in sorted(cluster_lane_stages.items())
            },
        )
        cluster_observations.append(cluster)
        for field_name in (
            "searched_record_ids",
            "selected_record_ids",
            "llm_exposed_record_ids",
            "memo_referenced_record_ids",
            "stock_cited_record_ids",
            "sector_cited_record_ids",
            "final_cited_record_ids",
            "selected_unused_record_ids",
            "offline_unexposed_searched_record_ids",
            "offline_unexposed_selected_record_ids",
            "offline_unexposed_llm_exposed_record_ids",
            "offline_unexposed_final_cited_record_ids",
            "rare_selected_record_ids",
        ):
            values = getattr(cluster, field_name)
            all_record_ids[field_name].update(values)
            occurrence_counts[field_name] += len(values)
        all_episode_ids.update(cluster.episode_ids)
        all_independent_unit_ids.update(cluster.independent_unit_ids)
        all_year_counts.update(cluster.year_counts)
        for lane_name, stages in cluster.lane_stage_counts.items():
            lane_stages[lane_name].update(stages)
        lanes.update(trace.lane_selected_counts)
        base["online_full_scan_count"] += trace.online_full_scan_count
        base["blind_web_call_count"] += trace.blind_web_call_count
    if require_complete_provenance and base["unresolved_episode_record_ids"]:
        raise ValueError("runtime retrieval records lack canonical episode namespaces")
    cluster_observations.sort(key=lambda item: item.cluster_id)
    retrieval = RetrievalCaseObservation(
        memory_snapshot_id=context.memory_snapshot_id,
        adaptive_trace_count=len(context.adaptive_retrieval_traces),
        evidence_pack_manifest_path=base["evidence_pack_manifest_path"],
        evidence_pack_manifest_sha256=base["evidence_pack_manifest_sha256"],
        evidence_assignment_count=base["evidence_assignment_count"],
        evidence_unique_record_count=base["evidence_unique_record_count"],
        evidence_packed_call_count=base["evidence_packed_call_count"],
        evidence_provider_checkpoint_count=base[
            "evidence_provider_checkpoint_count"
        ],
        evidence_avoided_payload_occurrence_count=base[
            "evidence_avoided_payload_occurrence_count"
        ],
        clusters=cluster_observations,
        searched_record_ids=sorted(all_record_ids["searched_record_ids"]),
        selected_record_ids=sorted(all_record_ids["selected_record_ids"]),
        llm_exposed_record_ids=sorted(all_record_ids["llm_exposed_record_ids"]),
        memo_referenced_record_ids=sorted(
            all_record_ids["memo_referenced_record_ids"]
        ),
        stock_cited_record_ids=sorted(all_record_ids["stock_cited_record_ids"]),
        sector_cited_record_ids=sorted(all_record_ids["sector_cited_record_ids"]),
        final_cited_record_ids=sorted(all_record_ids["final_cited_record_ids"]),
        selected_unused_record_ids=sorted(
            all_record_ids["selected_unused_record_ids"]
        ),
        offline_unexposed_searched_record_ids=sorted(
            all_record_ids["offline_unexposed_searched_record_ids"]
        ),
        offline_unexposed_selected_record_ids=sorted(
            all_record_ids["offline_unexposed_selected_record_ids"]
        ),
        offline_unexposed_llm_exposed_record_ids=sorted(
            all_record_ids["offline_unexposed_llm_exposed_record_ids"]
        ),
        offline_unexposed_final_cited_record_ids=sorted(
            all_record_ids["offline_unexposed_final_cited_record_ids"]
        ),
        rare_selected_record_ids=sorted(all_record_ids["rare_selected_record_ids"]),
        independent_unit_ids=sorted(all_independent_unit_ids),
        episode_ids=sorted(all_episode_ids),
        year_counts=dict(sorted(all_year_counts.items())),
        lane_stage_counts={
            lane: dict(sorted(stages.items()))
            for lane, stages in sorted(lane_stages.items())
        },
        searched_record_occurrence_count=occurrence_counts["searched_record_ids"],
        selected_record_occurrence_count=occurrence_counts["selected_record_ids"],
        llm_exposed_record_occurrence_count=occurrence_counts[
            "llm_exposed_record_ids"
        ],
        memo_referenced_record_occurrence_count=occurrence_counts[
            "memo_referenced_record_ids"
        ],
        stock_cited_record_occurrence_count=occurrence_counts[
            "stock_cited_record_ids"
        ],
        sector_cited_record_occurrence_count=occurrence_counts[
            "sector_cited_record_ids"
        ],
        final_cited_record_occurrence_count=occurrence_counts[
            "final_cited_record_ids"
        ],
        selected_unused_record_occurrence_count=occurrence_counts[
            "selected_unused_record_ids"
        ],
    )
    if final_manifest is not None:
        if final_manifest.get("selected_record_count") != (
            retrieval.selected_record_occurrence_count
        ):
            raise ValueError("runtime final manifest total selected count is stale")
        if final_manifest.get("final_cited_record_count") != (
            retrieval.final_cited_record_occurrence_count
        ):
            raise ValueError("runtime final manifest total citation count is stale")
        if int(final_manifest.get("blind_web_call_count") or 0):
            raise ValueError("runtime final manifest contains BLIND web calls")
        if int(final_manifest.get("online_full_scan_count") or 0) != (
            base["online_full_scan_count"]
        ):
            raise ValueError("runtime final manifest full-scan count is stale")
    base.update(
        {
            "searched_record_count": retrieval.searched_record_occurrence_count,
            "searched_record_unique_count": len(retrieval.searched_record_ids),
            "selected_record_count": retrieval.selected_record_occurrence_count,
            "selected_record_unique_count": len(retrieval.selected_record_ids),
            "llm_exposed_record_count": (
                retrieval.llm_exposed_record_occurrence_count
            ),
            "memo_referenced_record_count": (
                retrieval.memo_referenced_record_occurrence_count
            ),
            "stock_cited_record_count": retrieval.stock_cited_record_occurrence_count,
            "sector_cited_record_count": (
                retrieval.sector_cited_record_occurrence_count
            ),
            "selected_unused_record_count": (
                retrieval.selected_unused_record_occurrence_count
            ),
            "offline_unexposed_candidate_count": sum(
                len(cluster.offline_unexposed_searched_record_ids)
                for cluster in retrieval.clusters
            ),
            "offline_unexposed_recovered_count": sum(
                len(cluster.offline_unexposed_selected_record_ids)
                for cluster in retrieval.clusters
            ),
            "offline_unexposed_llm_exposed_count": sum(
                len(cluster.offline_unexposed_llm_exposed_record_ids)
                for cluster in retrieval.clusters
            ),
            "offline_unexposed_final_cited_count": sum(
                len(cluster.offline_unexposed_final_cited_record_ids)
                for cluster in retrieval.clusters
            ),
            "rare_mechanism_recovered_count": sum(
                len(cluster.rare_selected_record_ids)
                for cluster in retrieval.clusters
            ),
            "final_cited_record_count": (
                retrieval.final_cited_record_occurrence_count
            ),
            "final_cited_unique_record_count": len(
                retrieval.final_cited_record_ids
            ),
            "lane_selected_counts": dict(sorted(lanes.items())),
            "lane_stage_counts": retrieval.lane_stage_counts,
            "selected_independent_unit_ids": set(
                retrieval.independent_unit_ids
            ),
            "selected_episode_ids": set(retrieval.episode_ids),
            "selected_year_counts": Counter(retrieval.year_counts),
            "snapshot_closure_verified": True,
            "artifact_closure_verified": True,
            "cluster_observations": [
                item.model_dump(mode="json") for item in retrieval.clusters
            ],
            "retrieval_observation": retrieval.model_dump(mode="json"),
        }
    )
    summary = manifest.daily_memory_context_summary
    if isinstance(summary, dict):
        expected_final_cited = summary.get("runtime_final_cited_record_count")
        if (
            isinstance(expected_final_cited, int)
            and expected_final_cited != base["final_cited_record_count"]
        ):
            raise ValueError("runtime final trace citation count differs from context summary")
    return base


def _runtime_trace_paths(
    root: Path,
    *,
    manifest: Any,
    context: DailyMemoryContext,
) -> list[Path]:
    """Prefer the post-synthesis traces committed by the final trace manifest."""

    entries, _manifest = _runtime_trace_entries(
        root,
        manifest=manifest,
        context=context,
    )
    return [entry["path"] for entry in entries]


def _runtime_trace_entries(
    root: Path,
    *,
    manifest: Any,
    context: DailyMemoryContext,
    expected_prediction_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Return hash-closed trace entries and the optional final manifest."""

    summary = manifest.daily_memory_context_summary
    if not isinstance(summary, dict):
        return _initial_runtime_trace_entries(root, context), None
    manifest_ref = summary.get("runtime_retrieval_final_manifest_artifact")
    manifest_sha256 = summary.get("runtime_retrieval_final_manifest_sha256")
    if manifest_ref is None and manifest_sha256 is None:
        return _initial_runtime_trace_entries(root, context), None
    if not isinstance(manifest_ref, str) or not isinstance(manifest_sha256, str):
        raise ValueError("runtime final trace manifest reference is incomplete")
    _final_manifest_path, final_manifest_bytes = _read_verified_runtime_artifact(
        root,
        artifact_path=manifest_ref,
        artifact_sha256=manifest_sha256,
        label="runtime final trace manifest",
    )
    payload = _decode_json_bytes(
        final_manifest_bytes,
        label="runtime final trace manifest",
    )
    traces = payload.get("traces") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version")
        != "nslab.runtime_retrieval_final_manifest.v1"
        or payload.get("run_id") != context.run_id
        or not isinstance(traces, list)
        or payload.get("trace_count") != len(traces)
    ):
        raise ValueError("runtime final trace manifest is invalid")
    if (
        expected_prediction_id is not None
        and payload.get("prediction_id") != expected_prediction_id
    ):
        raise ValueError("runtime final trace manifest prediction identity mismatch")
    entries: list[dict[str, Any]] = []
    cluster_ids: list[str] = []
    for row in traces:
        if not isinstance(row, dict):
            raise ValueError("runtime final trace reference is invalid")
        artifact_path = row.get("artifact_path")
        artifact_sha256 = row.get("sha256")
        cluster_id = row.get("cluster_id")
        if (
            not isinstance(artifact_path, str)
            or not artifact_path
            or not isinstance(artifact_sha256, str)
            or not artifact_sha256
            or not isinstance(cluster_id, str)
            or not cluster_id
        ):
            raise ValueError("runtime final trace reference is incomplete")
        path, trace_bytes = _read_verified_runtime_artifact(
            root,
            artifact_path=artifact_path,
            artifact_sha256=artifact_sha256,
            label="runtime final trace",
        )
        entries.append({**row, "path": path, "payload_bytes": trace_bytes})
        cluster_ids.append(cluster_id)
    if cluster_ids != context.runtime_retrieval_cluster_ids:
        raise ValueError("runtime final trace cluster coverage mismatch")
    return entries, payload


def _initial_runtime_trace_entries(
    root: Path,
    context: DailyMemoryContext,
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for reference in context.runtime_retrieval_traces:
        path, trace_bytes = _read_verified_runtime_artifact(
            root,
            artifact_path=reference.artifact_path,
            artifact_sha256=reference.sha256,
            label="runtime retrieval trace",
        )
        trace = RuntimeRetrievalTrace.model_validate(
            _decode_json_bytes(trace_bytes, label="runtime retrieval trace")
        )
        entries.append(
            {
                "cluster_id": trace.cluster_id,
                "artifact_path": reference.artifact_path,
                "sha256": reference.sha256,
                "selected_record_count": None,
                "final_cited_record_count": None,
                "path": path,
                "payload_bytes": trace_bytes,
            }
        )
    if [str(entry["cluster_id"]) for entry in entries] != (
        context.runtime_retrieval_cluster_ids
    ):
        raise ValueError("runtime retrieval trace cluster coverage mismatch")
    return entries


def _verified_runtime_artifact(
    root: Path,
    *,
    artifact_path: str,
    artifact_sha256: str,
    label: str,
) -> Path:
    path, _payload_bytes = _read_verified_runtime_artifact(
        root,
        artifact_path=artifact_path,
        artifact_sha256=artifact_sha256,
        label=label,
    )
    return path


def _verify_runtime_evidence_pack_graph(
    root: Path,
    *,
    manifest: RuntimeEvidencePackManifest,
) -> int:
    """Verify the immutable pre-call plan, normalized outputs, and provider checkpoints."""

    _plan_path, plan_bytes = _read_verified_runtime_artifact(
        root,
        artifact_path=manifest.plan.artifact_path,
        artifact_sha256=manifest.plan.sha256,
        label="runtime evidence pack plan",
    )
    plan = RuntimeEvidencePackPlan.model_validate(
        _decode_json_bytes(plan_bytes, label="runtime evidence pack plan")
    )
    if manifest.plan.item_count != len(plan.packs):
        raise ValueError("runtime evidence pack plan item count is stale")
    if (
        plan.run_id != manifest.run_id
        or as_kst(plan.cutoff_at) != as_kst(manifest.cutoff_at)
        or plan.memory_snapshot_id != manifest.memory_snapshot_id
        or plan.policy_version != manifest.policy_version
        or plan.max_prompt_chars != manifest.max_prompt_chars
        or plan.assignment_count != manifest.assignment_count
        or plan.unique_record_count != manifest.unique_record_count
        or plan.unpacked_payload_occurrence_count
        != manifest.unpacked_payload_occurrence_count
        or plan.planned_payload_occurrence_count
        != manifest.packed_payload_occurrence_count
        or plan.avoided_payload_occurrence_count
        != manifest.avoided_payload_occurrence_count
        or plan.assignment_root_sha256 != manifest.assignment_root_sha256
        or plan.source_record_root_sha256 != manifest.source_record_root_sha256
        or len(plan.packs) != len(manifest.packs)
    ):
        raise ValueError("runtime evidence pack plan differs from its manifest")
    checkpoint_count = 0
    for planned, completed in zip(plan.packs, manifest.packs, strict=True):
        completed_plan_fields = {
            "pack_id": completed.pack_id,
            "purpose": completed.purpose,
            "prompt_sha256": completed.prompt_sha256,
            "prompt_chars": completed.prompt_chars,
            "cluster_ids": completed.cluster_ids,
            "source_record_ids": completed.source_record_ids,
            "assignment_count": completed.assignment_count,
            "assignment_root_sha256": completed.assignment_root_sha256,
        }
        if planned.model_dump(mode="json") != completed_plan_fields:
            raise ValueError("runtime evidence completed pack differs from its plan")
        _output_path, output_bytes = _read_verified_runtime_artifact(
            root,
            artifact_path=completed.output.artifact_path,
            artifact_sha256=completed.output.sha256,
            label="runtime evidence normalized pack output",
        )
        output = RuntimeEvidenceMemoPack.model_validate(
            _decode_json_bytes(
                output_bytes,
                label="runtime evidence normalized pack output",
            )
        )
        if (
            completed.output.item_count != len(output.batches)
            or output.cluster_ids != completed.cluster_ids
            or output.source_record_ids != completed.source_record_ids
        ):
            raise ValueError("runtime evidence normalized pack output is stale")
        checkpoint = completed.provider_checkpoint
        if checkpoint is None:
            continue
        checkpoint_path, checkpoint_bytes = _read_verified_runtime_artifact(
            root,
            artifact_path=checkpoint.artifact_path,
            artifact_sha256=checkpoint.sha256,
            label="runtime evidence provider checkpoint",
        )
        checkpoint_payload = _decode_json_bytes(
            checkpoint_bytes,
            label="runtime evidence provider checkpoint",
        )
        if not isinstance(checkpoint_payload, dict):
            raise ValueError("runtime evidence provider checkpoint is not an object")
        checkpoint_input = checkpoint_payload.get("input")
        checkpoint_output = checkpoint_payload.get("output")
        if not isinstance(checkpoint_input, dict) or not isinstance(
            checkpoint_output, dict
        ):
            raise ValueError("runtime evidence provider checkpoint payload is invalid")
        if (
            checkpoint.item_count != 1
            or checkpoint_path.stem != completed.provider_checkpoint_id
            or checkpoint_payload.get("schema_version")
            != "nslab.llm_checkpoint.v1"
            or checkpoint_payload.get("checkpoint_id")
            != completed.provider_checkpoint_id
            or checkpoint_payload.get("operation") != "generate_structured"
            or checkpoint_payload.get("purpose") != completed.purpose
            or checkpoint_payload.get("status") != "ok"
            or checkpoint_input.get("prompt_sha256")
            != completed.prompt_sha256
            or checkpoint_input.get("prompt_chars") != completed.prompt_chars
            or checkpoint_input.get("response_model")
            != RuntimeEvidenceMemoPack.__name__
            or checkpoint_payload.get("input_sha256")
            != sha256_text(canonical_json(checkpoint_input))
            or checkpoint_payload.get("output_sha256")
            != completed.provider_output_sha256
            or sha256_text(canonical_json(checkpoint_output))
            != completed.provider_output_sha256
        ):
            raise ValueError("runtime evidence provider checkpoint commitment drifted")
        RuntimeEvidenceMemoPack.model_validate(checkpoint_output)
        checkpoint_count += 1
    return checkpoint_count


def _read_verified_runtime_artifact(
    root: Path,
    *,
    artifact_path: str,
    artifact_sha256: str,
    label: str,
) -> tuple[Path, bytes]:
    root = root.resolve()
    path = (root / artifact_path).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} escapes the project root") from exc
    try:
        payload_bytes = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"{label} is missing") from exc
    if sha256_bytes(payload_bytes) != artifact_sha256:
        raise ValueError(f"{label} hash mismatch")
    return path, payload_bytes


def _runtime_memo_ids_by_record(
    root: Path,
    *,
    trace: RuntimeRetrievalTrace,
    require_complete: bool,
) -> dict[str, set[str]]:
    selected = {
        row.record_id for row in trace.rows if "LANE_SELECTED" in row.stages
    }
    reference = trace.evidence_memo_artifact
    if reference is None:
        if selected and require_complete:
            raise ValueError("selected runtime records have no evidence memo artifact")
        return {}
    _path, memo_bytes = _read_verified_runtime_artifact(
        root,
        artifact_path=reference.artifact_path,
        artifact_sha256=reference.sha256,
        label="runtime evidence memo",
    )
    memos: list[RuntimeEvidenceMemo] = []
    try:
        memo_lines = memo_bytes.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise ValueError("runtime evidence memo is not valid UTF-8") from exc
    for line_number, line in enumerate(memo_lines, start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
            memo = RuntimeEvidenceMemo.model_validate(payload)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(
                f"runtime evidence memo row {line_number} is invalid"
            ) from exc
        if memo.cluster_id != trace.cluster_id:
            raise ValueError("runtime evidence memo cluster identity mismatch")
        memos.append(memo)
    if reference.item_count != len(memos):
        raise ValueError("runtime evidence memo item count is stale")
    memo_ids = [memo.memo_id for memo in memos]
    if len(memo_ids) != len(set(memo_ids)):
        raise ValueError("runtime evidence memo IDs are duplicated")
    by_record: dict[str, set[str]] = defaultdict(set)
    for memo in memos:
        for record_id in memo.source_record_ids:
            by_record[record_id].add(memo.memo_id)
    if set(by_record) != selected:
        raise ValueError("runtime evidence memos do not close over selected records")
    return by_record


def _episode_id_from_record_id(record_id: str) -> str | None:
    episode_id, separator, local_id = record_id.rpartition("__")
    if not separator or not episode_id.strip() or not local_id.strip():
        return None
    return episode_id


def _prediction_metrics(
    prediction: BlindPrediction,
    truth_path: Path | None,
    *,
    truth_bytes: bytes | None = None,
    evaluation_universe_tickers: Sequence[str] | None = None,
    probability_policy_version: str | None = None,
) -> dict[str, Any]:
    outcome = _load_canonical_outcome_universe(
        truth_path,
        truth_bytes=truth_bytes,
        trade_date=prediction.trade_date,
    )
    if probability_policy_version is not None and evaluation_universe_tickers is None:
        raise ValueError("formal probability scoring requires a sealed universe")
    if evaluation_universe_tickers is None:
        sealed_d1_universe = list(outcome.raw_tickers)
    else:
        sealed_d1_universe = [
            _canonical_ticker(value, field_name="evaluation universe")
            for value in evaluation_universe_tickers
        ]
        if sealed_d1_universe != sorted(set(sealed_d1_universe)):
            raise ValueError("evaluation universe must be sorted and unique")
    sealed_d1_tickers = set(sealed_d1_universe)
    raw_outcome_tickers = set(outcome.raw_tickers)
    evaluation_universe = sorted(sealed_d1_tickers & raw_outcome_tickers)
    if not evaluation_universe:
        raise ValueError("D-1 and outcome universes do not overlap")
    scorable_universe = sorted(set(evaluation_universe) & set(outcome.tickers))
    if not scorable_universe:
        raise ValueError("evaluation universe has no eligible outcome rows")
    universe_root_sha256 = sha256_text(canonical_json(evaluation_universe))
    scorable_universe_root_sha256 = sha256_text(canonical_json(scorable_universe))
    upper_limit_targets = outcome.upper_limit_tickers & set(scorable_universe)
    high20_targets = outcome.high20_tickers & set(scorable_universe)
    high10_targets = outcome.high10_tickers & set(scorable_universe)
    ranked_candidates = sorted(prediction.candidates, key=lambda item: item.rank)
    ranks = [item.rank for item in ranked_candidates]
    if ranks != list(range(1, len(ranked_candidates) + 1)):
        raise ValueError("prediction candidate ranks must be unique and contiguous")
    ranked = [
        _canonical_ticker(item.ticker, field_name="prediction ticker")
        for item in ranked_candidates
    ]
    if len(ranked) != len(set(ranked)):
        raise ValueError("prediction candidate tickers must be unique")
    unexpected = sorted(set(ranked) - sealed_d1_tickers)
    if unexpected:
        raise ValueError(
            "prediction contains tickers outside the sealed D-1 universe: "
            + ",".join(unexpected)
        )
    outcome_missing_ranked = sorted(set(ranked) - raw_outcome_tickers)
    if outcome_missing_ranked:
        raise ValueError(
            "prediction contains tickers without an outcome row: "
            + ",".join(outcome_missing_ranked)
        )
    ineligible_outcome_ranked = sorted(set(ranked) - set(scorable_universe))
    ranked_top20 = ranked[:20]
    probabilities_by_ticker = {
        ticker: QUALITY_HIGH20_CONFIDENCE_PROBABILITIES[
            str(candidate.confidence_label).lower()
        ]
        for ticker, candidate in zip(ranked_top20, ranked_candidates[:20], strict=True)
    }
    selective_pairs: list[dict[str, float | str]] = [
        {
            "ticker": ticker,
            "probability": probabilities_by_ticker[ticker],
            "outcome": float(ticker in high20_targets),
        }
        for ticker in ranked_top20
        if ticker in scorable_universe
    ]
    selective_brier = _brier_score(selective_pairs)
    selective_ece = _calibration_error(selective_pairs)
    population_pairs: list[dict[str, float | str]] = []
    population_brier: float | None = None
    population_ece: float | None = None
    climatology_brier: float | None = None
    brier_skill: float | None = None
    probability_status = "UNAVAILABLE_PROBABILITY_SEMANTICS_NOT_SEALED"
    if probability_policy_version is not None:
        if probability_policy_version != QUALITY_HIGH20_PROBABILITY_POLICY_VERSION:
            raise ValueError("unsupported QUALITY_FULL probability policy")
        probability_status = "AVAILABLE_SEALED_UNCONDITIONAL_HIGH20_PROBABILITY"
        population_pairs = [
            {
                "ticker": ticker,
                "probability": probabilities_by_ticker.get(ticker, 0.0),
                "outcome": float(ticker in outcome.high20_tickers),
            }
            for ticker in scorable_universe
        ]
        population_brier = _brier_score(population_pairs)
        population_ece = _calibration_error(population_pairs)
        prevalence = len(high20_targets) / len(scorable_universe)
        climatology_brier = prevalence * (1.0 - prevalence)
        if population_brier is not None and climatology_brier:
            brier_skill = 1.0 - population_brier / climatology_brier
    reciprocal_rank = next(
        (
            1.0 / rank
            for rank, ticker in enumerate(ranked_top20, start=1)
            if ticker in high20_targets
        ),
        0.0,
    )
    leader_rank = next(
        (
            rank
            for rank, ticker in enumerate(ranked, start=1)
            if ticker == outcome.leader_ticker
        ),
        None,
    )
    top_pick = ranked[0] if ranked else None
    metrics: dict[str, Any] = {
        "recall_at_20": _recall_at(ranked, set(high20_targets), 20),
        "precision_at_20": _precision_at(
            ranked,
            set(high20_targets),
            20,
        ),
        "mrr": reciprocal_rank,
        "brier": population_brier if population_brier is not None else selective_brier,
        "population_brier": population_brier,
        "population_brier_sum": (
            population_brier * len(scorable_universe)
            if population_brier is not None
            else None
        ),
        "population_expected_calibration_error": population_ece,
        "population_calibration_bins": _calibration_bins(population_pairs),
        "population_climatology_brier": climatology_brier,
        "population_climatology_brier_sum": (
            climatology_brier * len(scorable_universe)
            if climatology_brier is not None
            else None
        ),
        "population_brier_skill_vs_climatology": brier_skill,
        "population_count": len(scorable_universe),
        "population_positive_count": len(high20_targets),
        "population_universe_sha256": scorable_universe_root_sha256,
        "population_universe_policy_version": (
            QUALITY_BRIER_POPULATION_POLICY_VERSION
        ),
        "population_probability_status": probability_status,
        "probability_policy_version": probability_policy_version,
        "selective_top20_brier": selective_brier,
        "selective_top20_expected_calibration_error": selective_ece,
        "calibration_absolute_error": _calibration_absolute_error(
            selective_pairs
        ),
        "calibration_pairs": selective_pairs,
        "leader_selection_accuracy": float(top_pick == outcome.leader_ticker),
        "leader_truth_ticker": outcome.leader_ticker,
        "leader_rank": leader_rank,
        "leader_mrr": 1.0 / leader_rank if leader_rank is not None else 0.0,
        "top_pick_upper_limit_hit": float(
            top_pick is not None and top_pick in upper_limit_targets
        ),
        "top_pick_high20_hit": float(
            top_pick is not None and top_pick in high20_targets
        ),
        "leader_upper_limit_hit": float(
            top_pick is not None and top_pick in upper_limit_targets
        ),
        "leader_high20_hit": float(
            top_pick is not None and top_pick in high20_targets
        ),
        "generated_candidate_tickers": ranked,
        "ranked_candidate_tickers": ranked,
        "evaluation_universe_count": len(evaluation_universe),
        "evaluation_universe_sha256": universe_root_sha256,
        "evaluation_universe_policy_version": (
            QUALITY_MARKET_UNIVERSE_POLICY_VERSION
        ),
        "sealed_d1_universe_count": len(sealed_d1_universe),
        "sealed_d1_universe_sha256": sha256_text(
            canonical_json(sealed_d1_universe)
        ),
        "raw_outcome_universe_count": len(outcome.raw_tickers),
        "raw_outcome_universe_sha256": sha256_text(
            canonical_json(list(outcome.raw_tickers))
        ),
        "d1_tickers_missing_outcome_count": len(
            sealed_d1_tickers - raw_outcome_tickers
        ),
        "outcome_tickers_missing_d1_count": len(
            raw_outcome_tickers - sealed_d1_tickers
        ),
        "outcome_ineligible_selected_tickers": ineligible_outcome_ranked,
        "outcome_ineligible_selected_count": len(ineligible_outcome_ranked),
        "outcome_universe_sha256": outcome.universe_root_sha256,
        "excluded_outcome_tickers": sorted(
            set(outcome.excluded_tickers) & sealed_d1_tickers
        ),
        "upper_limit_target_count": len(upper_limit_targets),
        "high20_target_count": len(high20_targets),
        "high10_target_count": len(high10_targets),
        "upper_limit_target_tickers": sorted(upper_limit_targets),
        "high20_target_tickers": sorted(high20_targets),
        "high10_target_tickers": sorted(high10_targets),
        "legacy_metric_aliases": {
            "leader_upper_limit_hit": "top_pick_upper_limit_hit",
            "leader_high20_hit": "top_pick_high20_hit",
            "brier": (
                "population_brier"
                if population_brier is not None
                else "selective_top20_brier"
            ),
        },
    }
    for cutoff in (5, 10, 20):
        selected = set(ranked[:cutoff])
        metrics[f"upper_limit_hit_count_at_{cutoff}"] = len(
            selected.intersection(upper_limit_targets)
        )
        metrics[f"high20_hit_count_at_{cutoff}"] = len(
            selected.intersection(high20_targets)
        )
        metrics[f"high10_hit_count_at_{cutoff}"] = len(
            selected.intersection(high10_targets)
        )
        metrics[f"selected_count_at_{cutoff}"] = cutoff
        metrics[f"actual_selected_count_at_{cutoff}"] = len(ranked[:cutoff])
        metrics[f"outcome_ineligible_selected_count_at_{cutoff}"] = len(
            selected.intersection(outcome.excluded_tickers)
        )
        metrics[f"upper_limit_recall_at_{cutoff}"] = _recall_at(
            ranked,
            set(upper_limit_targets),
            cutoff,
        )
        metrics[f"high20_recall_at_{cutoff}"] = _recall_at(
            ranked,
            set(high20_targets),
            cutoff,
        )
        metrics[f"high10_recall_at_{cutoff}"] = _recall_at(
            ranked,
            set(high10_targets),
            cutoff,
        )
        metrics[f"high20_precision_at_{cutoff}"] = _precision_at(
            ranked,
            set(high20_targets),
            cutoff,
        )
        metrics[f"leader_recall_at_{cutoff}"] = float(
            outcome.leader_ticker in selected
        )
        metrics[f"max_return_tie_aware_hit_at_{cutoff}"] = float(
            bool(selected.intersection(outcome.max_return_tickers))
        )
        for name, target in (
            ("upper_limit", upper_limit_targets),
            ("high20", high20_targets),
            ("high10", high10_targets),
        ):
            metrics[f"{name}_no_positive_false_positive_count_at_{cutoff}"] = (
                len(ranked[:cutoff]) if not target else 0
            )
    return metrics


def _load_canonical_outcome_universe(
    truth_path: Path | None,
    *,
    truth_bytes: bytes | None = None,
    trade_date: date,
) -> CanonicalOutcomeUniverse:
    all_rows_by_ticker: dict[str, dict[str, Any]] = {}
    rows_by_ticker: dict[str, dict[str, Any]] = {}
    seen_tickers: set[str] = set()
    outcome_ids: set[str] = set()
    excluded: list[str] = []
    if (truth_path is None) == (truth_bytes is None):
        raise ValueError("outcome ledger requires exactly one verified input")
    if truth_bytes is not None:
        try:
            lines = truth_bytes.decode("utf-8-sig").splitlines()
        except UnicodeDecodeError as exc:
            raise ValueError("outcome ledger is not valid UTF-8") from exc
    else:
        assert truth_path is not None
        lines = truth_path.read_text(encoding="utf-8-sig").splitlines()
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"outcome ledger row {line_number} is not valid JSON"
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError(f"outcome ledger row {line_number} is not an object")
        ticker = _canonical_outcome_ticker(raw, line_number=line_number)
        if ticker in seen_tickers:
            raise ValueError(f"outcome ledger duplicate canonical ticker: {ticker}")
        seen_tickers.add(ticker)
        outcome_id = _required_alias_string(
            raw,
            aliases=("outcome_row_id", "outcome_id"),
            label="outcome ID",
            line_number=line_number,
        )
        if outcome_id in outcome_ids:
            raise ValueError(f"outcome ledger duplicate outcome ID: {outcome_id}")
        outcome_ids.add(outcome_id)
        canonical = _canonical_outcome_row(
            raw,
            ticker=ticker,
            outcome_id=outcome_id,
            line_number=line_number,
            trade_date=trade_date,
        )
        all_rows_by_ticker[ticker] = canonical
        if canonical["eligible"] is True:
            rows_by_ticker[ticker] = canonical
        else:
            excluded.append(ticker)
    if not rows_by_ticker:
        raise ValueError("outcome ledger has no eligible market universe")
    ranks = [int(row["high_return_rank"]) for row in rows_by_ticker.values()]
    if sorted(ranks) != list(range(1, len(rows_by_ticker) + 1)):
        raise ValueError("eligible outcome high-return ranks are not contiguous")
    leaders = [
        ticker
        for ticker, row in rows_by_ticker.items()
        if int(row["high_return_rank"]) == 1
    ]
    if len(leaders) != 1:
        raise ValueError("eligible outcome universe must have one exact rank-1 leader")
    high_values = {
        ticker: float(row["high_return_pct"])
        for ticker, row in rows_by_ticker.items()
    }
    maximum = max(high_values.values())
    max_return_tickers = frozenset(
        ticker for ticker, value in high_values.items() if value == maximum
    )
    upper_limit = frozenset(
        ticker
        for ticker, row in rows_by_ticker.items()
        if row["upper_limit_touched"] is True
    )
    high20 = frozenset(
        ticker
        for ticker, row in rows_by_ticker.items()
        if float(row["high_return_pct"]) >= 20.0
        or row["upper_limit_touched"] is True
    )
    high10 = frozenset(
        ticker
        for ticker, row in rows_by_ticker.items()
        if float(row["high_return_pct"]) >= 10.0
        or row["upper_limit_touched"] is True
    )
    universe = sorted(rows_by_ticker)
    return CanonicalOutcomeUniverse(
        trade_date=trade_date,
        all_rows_by_ticker=all_rows_by_ticker,
        rows_by_ticker=rows_by_ticker,
        excluded_tickers=tuple(sorted(excluded)),
        upper_limit_tickers=upper_limit,
        high20_tickers=high20,
        high10_tickers=high10,
        leader_ticker=leaders[0],
        max_return_tickers=max_return_tickers,
        universe_root_sha256=sha256_text(canonical_json(universe)),
    )


def _canonical_outcome_ticker(
    row: dict[str, Any],
    *,
    line_number: int,
) -> str:
    ticker_value = row.get("ticker")
    code_value = row.get("code")
    if ticker_value is None and code_value is None:
        raise ValueError(f"outcome ledger row {line_number} has no ticker/code")
    ticker = (
        _canonical_ticker(ticker_value, field_name="outcome ticker")
        if ticker_value is not None
        else None
    )
    code = (
        _canonical_ticker(code_value, field_name="outcome code")
        if code_value is not None
        else None
    )
    if ticker is not None and code is not None and ticker != code:
        raise ValueError(f"outcome ledger row {line_number} ticker/code mismatch")
    return ticker or code or ""


def _canonical_outcome_row(
    row: dict[str, Any],
    *,
    ticker: str,
    outcome_id: str,
    line_number: int,
    trade_date: date,
) -> dict[str, Any]:
    snapshot_date = row.get("snapshot_date")
    if snapshot_date != trade_date.isoformat():
        raise ValueError(f"outcome ledger row {line_number} trade date mismatch")
    for field_name in (
        "upper_limit_touched",
        "upper_limit_closed",
        "upper_limit_released",
        "corporate_action_warning",
        "new_listing_or_no_reference",
    ):
        if not isinstance(row.get(field_name), bool):
            raise ValueError(
                f"outcome ledger row {line_number} {field_name} is not boolean"
            )
    quality = _required_alias_string(
        row,
        aliases=("label_quality", "price_label_quality"),
        label="quality",
        line_number=line_number,
    ).casefold()
    data_quality = row.get("data_quality_status")
    if not isinstance(data_quality, str) or not data_quality.strip():
        raise ValueError(f"outcome ledger row {line_number} data quality is invalid")
    for optional_boolean in ("quarantined", "tradable"):
        if optional_boolean in row and not isinstance(row[optional_boolean], bool):
            raise ValueError(
                f"outcome ledger row {line_number} {optional_boolean} is not boolean"
            )
    quarantined = row.get("quarantined") is True
    explicitly_untradable = row.get("tradable") is False
    eligible = (
        data_quality.casefold() == "clean"
        and quality == "verified"
        and not quarantined
        and not explicitly_untradable
        and row["corporate_action_warning"] is False
        and row["new_listing_or_no_reference"] is False
    )
    exclusion_reasons = sorted(
        reason
        for excluded, reason in (
            (data_quality.casefold() != "clean", "DATA_QUALITY_NOT_CLEAN"),
            (quality != "verified", "LABEL_NOT_VERIFIED"),
            (quarantined, "QUARANTINED"),
            (explicitly_untradable, "EXPLICITLY_UNTRADABLE"),
            (row["corporate_action_warning"] is True, "CORPORATE_ACTION"),
            (
                row["new_listing_or_no_reference"] is True,
                "NEW_LISTING_OR_NO_REFERENCE",
            ),
        )
        if excluded
    )
    high_return: float | None = None
    high_rank: int | None = None
    if eligible:
        high_return = _finite_number(
            row.get("high_return_pct"),
            label="high_return_pct",
            line_number=line_number,
        )
        high_rank_number = _finite_number(
            row.get("high_return_rank"),
            label="high_return_rank",
            line_number=line_number,
        )
        high_rank = int(high_rank_number)
        if high_rank < 1 or high_rank_number != high_rank:
            raise ValueError(f"outcome ledger row {line_number} rank is invalid")
    return {
        **row,
        "ticker": ticker,
        "outcome_id": outcome_id,
        "high_return_pct": high_return,
        "high_return_rank": high_rank,
        "eligible": eligible,
        "exclusion_reasons": exclusion_reasons,
    }


def _required_alias_string(
    row: dict[str, Any],
    *,
    aliases: tuple[str, ...],
    label: str,
    line_number: int,
) -> str:
    values = [row.get(alias) for alias in aliases if row.get(alias) is not None]
    if not values or any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError(f"outcome ledger row {line_number} {label} is missing")
    normalized = {str(value).strip() for value in values}
    if len(normalized) != 1:
        raise ValueError(f"outcome ledger row {line_number} {label} aliases mismatch")
    return normalized.pop()


def _finite_number(value: Any, *, label: str, line_number: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"outcome ledger row {line_number} {label} is not numeric")
    observed = float(value)
    if not math.isfinite(observed):
        raise ValueError(f"outcome ledger row {line_number} {label} is not finite")
    return observed


def _canonical_ticker(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} is not a canonical ticker")
    text = value.strip()
    if not _OUTCOME_TICKER_PATTERN.fullmatch(text):
        raise ValueError(f"{field_name} is not a six-digit ticker")
    return text


def _brier_score(pairs: list[dict[str, float | str]]) -> float | None:
    if not pairs:
        return None
    return sum(
        (float(pair["probability"]) - float(pair["outcome"])) ** 2
        for pair in pairs
    ) / len(pairs)


def _calibration_absolute_error(
    pairs: list[dict[str, float | str]],
) -> float | None:
    if not pairs:
        return None
    confidence = sum(float(pair["probability"]) for pair in pairs) / len(pairs)
    accuracy = sum(float(pair["outcome"]) for pair in pairs) / len(pairs)
    return abs(confidence - accuracy)


def _calibration_error(
    pairs: list[dict[str, float | str]],
    *,
    bin_count: int = 5,
) -> float | None:
    bins = _calibration_bins(pairs, bin_count=bin_count)
    total = sum(int(row["count"]) for row in bins)
    if not total:
        return None
    return sum(
        int(row["count"])
        / total
        * abs(float(row["mean_probability"]) - float(row["mean_outcome"]))
        for row in bins
    )


def _calibration_bins(
    pairs: list[dict[str, float | str]],
    *,
    bin_count: int = 5,
) -> list[dict[str, float | int]]:
    result: list[dict[str, float | int]] = []
    for bin_index in range(bin_count):
        low = bin_index / bin_count
        high = (bin_index + 1) / bin_count
        members = [
            pair
            for pair in pairs
            if low <= float(pair["probability"])
            and (
                float(pair["probability"]) <= high
                if bin_index == bin_count - 1
                else float(pair["probability"]) < high
            )
        ]
        if not members:
            continue
        result.append(
            {
                "low": low,
                "high": high,
                "count": len(members),
                "mean_probability": sum(
                    float(pair["probability"]) for pair in members
                )
                / len(members),
                "mean_outcome": sum(float(pair["outcome"]) for pair in members)
                / len(members),
            }
        )
    return result


def _recall_at(
    ranked: list[str],
    target: set[str],
    cutoff: int,
) -> float | None:
    if not target:
        return None
    return len(set(ranked[:cutoff]).intersection(target)) / len(target)


def _precision_at(ranked: list[str], target: set[str], cutoff: int) -> float:
    if cutoff < 1:
        raise ValueError("precision cutoff must be positive")
    return len(set(ranked[:cutoff]).intersection(target)) / cutoff


def _final_memory_citation_rate(prediction: BlindPrediction) -> float | None:
    claims = [
        bool(candidate.memory_record_ids or candidate.prior_positive_record_ids or candidate.prior_negative_record_ids)
        for candidate in prediction.candidates
    ]
    return sum(claims) / len(claims) if claims else None


def _runtime_final_candidate_citation_rate(
    prediction: BlindPrediction,
    *,
    trace_stats: dict[str, Any],
) -> float | None:
    expected = {f"candidate:{candidate.rank}:{candidate.ticker}" for candidate in prediction.candidates}
    observed = trace_stats.get("runtime_final_candidate_ids")
    cited = set(observed) if isinstance(observed, set) else set()
    return len(expected.intersection(cited)) / len(expected) if expected else None


def _build_report(progress: dict[str, Any], *, expected_case_count: int) -> dict[str, Any]:
    observations = list(progress["observations"])
    by_variant = {variant: [row for row in observations if row["variant_id"] == variant] for variant in ("V0", "V1")}
    paired = len(by_variant["V0"]) == len(by_variant["V1"]) == expected_case_count
    formal_split_case_count = int(progress.get("identity", {}).get("formal_split_case_count") or expected_case_count)
    formal_split_closed = paired and expected_case_count == formal_split_case_count
    paired_rows: dict[str, dict[str, dict[str, Any]]] = {}
    for row in observations:
        paired_rows.setdefault(str(row["case_id"]), {})[str(row["variant_id"])] = row
    pre_retrieval_identity_match = paired and all(
        rows["V0"].get("pre_retrieval_identity") == rows["V1"].get("pre_retrieval_identity")
        for rows in paired_rows.values()
        if set(rows) >= {"V0", "V1"}
    )
    paired_source_identity_match = paired and all(
        tuple(rows["V0"].get(key) for key in ("news_sha256", "truth_sha256", "memory_snapshot_id"))
        == tuple(rows["V1"].get(key) for key in ("news_sha256", "truth_sha256", "memory_snapshot_id"))
        for rows in paired_rows.values()
        if set(rows) >= {"V0", "V1"}
    )

    def mean(rows: list[dict[str, Any]], key: str) -> float | None:
        values = [float(row["metrics"][key]) for row in rows if key in row["metrics"]]
        return sum(values) / len(values) if values else None

    metrics = {
        variant: {
            **{key: mean(rows, key) for key in _MARKET_METRIC_KEYS},
            "expected_calibration_error": _expected_calibration_error(rows),
            **_aggregate_micro_market_metrics(rows),
        }
        for variant, rows in by_variant.items()
    }
    future = sum(int(row["future_record_count"]) for row in observations)
    web = sum(int(row["blind_web_call_count"]) for row in observations)
    full_scan = sum(int(row["online_full_scan_count"]) for row in observations)
    v0_recovered = sum(int(row["offline_unexposed_recovered_count"]) for row in by_variant["V0"])
    recovered = sum(int(row["offline_unexposed_recovered_count"]) for row in by_variant["V1"])
    recovered_delta = recovered - v0_recovered
    variant_runtime: dict[str, dict[str, Any]] = {}
    for variant, rows in by_variant.items():
        elapsed = sorted(float(row["elapsed_seconds"]) for row in rows)
        lanes: Counter[str] = Counter()
        for row in rows:
            lanes.update(row["lane_selected_counts"])
        runtime_metrics = [row.get("runtime_metrics") for row in rows if isinstance(row.get("runtime_metrics"), dict)]
        pre_llm_latency = sorted(
            float(item["pre_llm_latency_seconds"])
            for item in runtime_metrics
            if isinstance(item.get("pre_llm_latency_seconds"), (int, float))
        )
        logical_calls = _sum_runtime_metric(
            runtime_metrics,
            "logical_llm_call_count",
        )
        checkpoint_hits = _sum_runtime_metric(
            runtime_metrics,
            "llm_checkpoint_hit_count",
        )
        years: Counter[str] = Counter()
        for row in rows:
            years.update(row.get("selected_year_counts") or {})
        selected_records = sum(int(row["selected_record_count"]) for row in rows)
        selected_units = sum(int(row.get("selected_independent_unit_count") or 0) for row in rows)
        offline_candidates = sum(int(row.get("offline_unexposed_candidate_count") or 0) for row in rows)
        offline_selected = sum(int(row["offline_unexposed_recovered_count"]) for row in rows)
        offline_llm_exposed = sum(int(row.get("offline_unexposed_llm_exposed_count") or 0) for row in rows)
        offline_final_cited = sum(int(row.get("offline_unexposed_final_cited_count") or 0) for row in rows)
        variant_runtime[variant] = {
            "adaptive_trace_count": sum(int(row["adaptive_trace_count"]) for row in rows),
            "runtime_trace_count": sum(int(row["runtime_trace_count"]) for row in rows),
            "selected_record_count": sum(int(row["selected_record_count"]) for row in rows),
            "selected_independent_unit_count": selected_units,
            "selected_year_counts": dict(sorted(years.items())),
            "selected_year_count": len(years),
            "issuer_day_duplicate_rate": (1.0 - selected_units / selected_records if selected_records else 0.0),
            "offline_unexposed_recovered_count": offline_selected,
            "offline_unexposed_candidate_count": offline_candidates,
            "offline_unexposed_selection_rate": (offline_selected / offline_candidates if offline_candidates else None),
            "offline_unexposed_llm_exposed_count": offline_llm_exposed,
            "offline_unexposed_runtime_exposure_rate": (
                offline_llm_exposed / offline_selected if offline_selected else None
            ),
            "offline_unexposed_final_cited_count": offline_final_cited,
            "offline_unexposed_final_citation_rate": (
                offline_final_cited / offline_selected if offline_selected else None
            ),
            "rare_mechanism_recovered_count": sum(int(row["rare_mechanism_recovered_count"]) for row in rows),
            "final_cited_record_count": sum(int(row["final_cited_record_count"]) for row in rows),
            "runtime_final_cited_candidate_count": sum(
                int(row.get("runtime_final_cited_candidate_count") or 0) for row in rows
            ),
            "lane_selected_counts": dict(sorted(lanes.items())),
            "mean_elapsed_seconds": (sum(elapsed) / len(elapsed) if elapsed else None),
            "p95_elapsed_seconds": _percentile(elapsed, 0.95),
            "total_prompt_token_count": sum(int(row["prompt_token_count"]) for row in rows),
            "logical_llm_call_count": logical_calls,
            "oauth_live_agent_call_count": _sum_runtime_metric(
                runtime_metrics,
                "oauth_live_agent_call_count",
            ),
            "llm_checkpoint_hit_count": checkpoint_hits,
            "llm_checkpoint_hit_rate": (checkpoint_hits / logical_calls if logical_calls else None),
            "oauth_cache_event_count": _sum_runtime_metric(
                runtime_metrics,
                "oauth_cache_event_count",
            ),
            "llm_prompt_tokens_estimate": _sum_runtime_metric(
                runtime_metrics,
                "llm_prompt_tokens_estimate",
            ),
            "llm_completion_tokens_estimate": _sum_runtime_metric(
                runtime_metrics,
                "llm_completion_tokens_estimate",
            ),
            "embedding_query_count": _sum_runtime_metric(
                runtime_metrics,
                "embedding_query_count",
            ),
            "embedding_text_count": _sum_runtime_metric(
                runtime_metrics,
                "embedding_text_count",
            ),
            "embedding_input_char_count": _sum_runtime_metric(
                runtime_metrics,
                "embedding_input_char_count",
            ),
            "process_peak_working_set_bytes": max(
                (
                    int(item["process_peak_working_set_bytes"])
                    for item in runtime_metrics
                    if isinstance(item.get("process_peak_working_set_bytes"), int)
                ),
                default=None,
            ),
            "peak_memory_scope": ("PROCESS_LIFETIME" if runtime_metrics else "UNAVAILABLE"),
            "pre_llm_latency_p50_seconds": _percentile(pre_llm_latency, 0.50),
            "pre_llm_latency_p95_seconds": _percentile(pre_llm_latency, 0.95),
            "pre_llm_latency_status": (
                "MEASURED_TO_FIRST_LLM_TRACE"
                if len(pre_llm_latency) == len(rows) and rows
                else "PARTIAL_OR_UNAVAILABLE"
            ),
        }
    gate_checks = {
        "paired_case_closure": paired,
        "formal_split_case_closure": formal_split_closed,
        "paired_source_identity_match": paired_source_identity_match,
        "paired_pre_retrieval_identity_match": pre_retrieval_identity_match,
        "future_leak_zero": future == 0,
        "blind_web_zero": web == 0,
        "online_full_scan_zero": full_scan == 0,
        "offline_unexposed_recovery_increased_vs_v0": recovered_delta > 0,
        "adaptive_trace_every_v1_case": bool(by_variant["V1"])
        and all(int(row["adaptive_trace_count"]) > 0 for row in by_variant["V1"]),
        "runtime_trace_every_v1_case": bool(by_variant["V1"])
        and all(int(row["runtime_trace_count"]) > 0 for row in by_variant["V1"]),
        "negative_or_counterexample_inclusion_nonzero": sum(
            int(variant_runtime["V1"]["lane_selected_counts"].get(lane, 0))
            for lane in ("NEGATIVE_CONTROL", "COUNTEREXAMPLE", "NEAR_MISS")
        )
        > 0,
        "final_memory_citation_at_least_95pct": (
            metrics["V1"]["final_memory_citation_rate"] is not None
            and float(metrics["V1"]["final_memory_citation_rate"]) >= 0.95
        ),
        "daily_p95_within_phase8_budget": (
            variant_runtime["V1"]["p95_elapsed_seconds"] is not None
            and float(variant_runtime["V1"]["p95_elapsed_seconds"]) <= SHADOW_DAILY_P95_BUDGET_MS / 1000.0
        ),
        "known_relevant_recall_labeled": False,
    }
    paired_bootstrap = _paired_bootstrap(
        observations,
        run_id=str(progress["run_id"]),
    )
    return {
        "schema_version": RUNTIME_VARIANT_SHADOW_VERSION,
        "run_id": progress["run_id"],
        "identity": progress["identity"],
        "expected_case_count": expected_case_count,
        "formal_split_case_count": formal_split_case_count,
        "evaluation_scope": progress.get("identity", {}).get(
            "evaluation_scope",
            "UNKNOWN",
        ),
        "completed_observation_count": len(observations),
        "paired_case_count": min(len(by_variant["V0"]), len(by_variant["V1"])),
        "variant_metrics": metrics,
        "variant_runtime": variant_runtime,
        "paired_bootstrap": paired_bootstrap,
        "v0_offline_unexposed_recovered_count": v0_recovered,
        "v1_offline_unexposed_recovered_count": recovered,
        "offline_unexposed_recovered_delta": recovered_delta,
        "future_record_count": future,
        "blind_web_call_count": web,
        "online_full_scan_count": full_scan,
        "known_relevant_recall_status": "UNAVAILABLE_REQUIRES_SEALED_RELEVANCE_LABELS",
        "theme_metric_status": "UNAVAILABLE_OUTCOME_LEDGER_HAS_NO_SECTOR_TRUTH",
        "newsless_hallucination_status": ("UNAVAILABLE_SELECTED_SHADOW_CASES_ALL_HAVE_CURRENT_NEWS"),
        "rare_recovery_metric_status": ("AUDIT_DEFINED_RARE_REASONING_PAYLOAD_PROXY_NOT_LABELED_RECALL"),
        "common_supporting_vector_search": "DISABLED_FORCE_EMPTY",
        "cost_status": "UNAVAILABLE_CODEX_OAUTH_NO_METERED_COST_RECEIPT",
        "system_metric_notes": {
            "prompt_and_completion_tokens": "CONSERVATIVE_ESTIMATES_FROM_LLM_TRACES",
            "oauth_calls": "LIVE_CODEX_AGENT_EXECUTIONS",
            "checkpoint_hits": "LOCAL_CONTENT_ADDRESSED_LLM_CHECKPOINT_REUSE",
            "embedding_queries": "LOCAL_SENTENCE_TRANSFORMER_ENCODE_INVOCATIONS",
            "peak_memory": "PROCESS_LIFETIME_HIGH_WATER_MARK",
            "pre_llm_latency": "ANALYSIS_START_TO_FIRST_LLM_TRACE",
        },
        "retrieval_metric_notes": {
            "year_diversity": "SOURCE_TRADE_DATE_FROM_RUNTIME_TRACE",
            "issuer_day_duplicate_rate": ("ONE_MINUS_DISTINCT_INDEPENDENT_UNITS_OVER_SELECTED_RECORDS"),
            "regime_diversity": "UNAVAILABLE_RUNTIME_TRACE_HAS_NO_REGIME_LABEL",
            "unsupported_memory_assertion_rate": ("UNAVAILABLE_NO_SEALED_CLAIM_LEVEL_SUPPORT_LABELS"),
        },
        "daily_p95_budget_seconds": SHADOW_DAILY_P95_BUDGET_MS / 1000.0,
        "gate_checks": gate_checks,
        "runtime_gate": "PASS" if all(gate_checks.values()) else "HOLD",
        "compiler_v8_decision": "HOLD_UNTIL_RUNTIME_GATE_PASS",
        "full_rebuild_decision": "HOLD",
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
    }


def _sum_runtime_metric(rows: list[dict[str, Any]], key: str) -> int:
    return sum(int(row.get(key) or 0) for row in rows if isinstance(row.get(key), int))


def _expected_calibration_error(
    rows: list[dict[str, Any]],
    *,
    bin_count: int = 5,
) -> float | None:
    pairs = [
        pair
        for row in rows
        for pair in row.get("metrics", {}).get("calibration_pairs", [])
        if isinstance(pair, dict)
        and isinstance(pair.get("probability"), (int, float))
        and isinstance(pair.get("outcome"), (int, float))
    ]
    if not pairs:
        return None
    error = 0.0
    for bin_index in range(bin_count):
        low = bin_index / bin_count
        high = (bin_index + 1) / bin_count
        members = [
            pair
            for pair in pairs
            if low <= float(pair["probability"])
            and (
                float(pair["probability"]) <= high if bin_index == bin_count - 1 else float(pair["probability"]) < high
            )
        ]
        if not members:
            continue
        confidence = sum(float(pair["probability"]) for pair in members) / len(members)
        accuracy = sum(float(pair["outcome"]) for pair in members) / len(members)
        error += len(members) / len(pairs) * abs(confidence - accuracy)
    return error


def _aggregate_micro_market_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, float | None]:
    result: dict[str, float | None] = {}
    targets = {
        "upper_limit": sum(int(row.get("metrics", {}).get("upper_limit_target_count") or 0) for row in rows),
        "high20": sum(int(row.get("metrics", {}).get("high20_target_count") or 0) for row in rows),
        "high10": sum(int(row.get("metrics", {}).get("high10_target_count") or 0) for row in rows),
    }
    for target, target_count in targets.items():
        result[f"micro_{target}_target_count"] = float(target_count)
        for cutoff in (5, 10, 20):
            hits = sum(int(row.get("metrics", {}).get(f"{target}_hit_count_at_{cutoff}") or 0) for row in rows)
            result[f"micro_{target}_recall_at_{cutoff}"] = hits / target_count if target_count else None
    for cutoff in (5, 10, 20):
        selected = sum(int(row.get("metrics", {}).get(f"selected_count_at_{cutoff}") or 0) for row in rows)
        high20_hits = sum(int(row.get("metrics", {}).get(f"high20_hit_count_at_{cutoff}") or 0) for row in rows)
        result[f"micro_high20_precision_at_{cutoff}"] = high20_hits / selected if selected else None
    population_count = sum(
        int(row.get("metrics", {}).get("population_count") or 0) for row in rows
    )
    population_brier_sum = sum(
        float(row.get("metrics", {}).get("population_brier_sum") or 0.0)
        for row in rows
    )
    climatology_brier_sum = sum(
        float(
            row.get("metrics", {}).get("population_climatology_brier_sum")
            or 0.0
        )
        for row in rows
    )
    population_available = all(
        isinstance(row.get("metrics", {}).get("population_brier_sum"), (int, float))
        for row in rows
    )
    result["micro_population_brier"] = (
        population_brier_sum / population_count
        if population_count and population_available
        else None
    )
    result["micro_population_climatology_brier"] = (
        climatology_brier_sum / population_count
        if population_count and population_available
        else None
    )
    result["micro_population_brier_skill_vs_climatology"] = (
        1.0 - population_brier_sum / climatology_brier_sum
        if population_available and climatology_brier_sum
        else None
    )
    result["micro_population_expected_calibration_error"] = (
        _aggregate_population_calibration_error(rows)
        if population_available
        else None
    )
    return result


def _aggregate_population_calibration_error(
    rows: list[dict[str, Any]],
) -> float | None:
    bins: dict[tuple[float, float], dict[str, float]] = {}
    for row in rows:
        raw_bins = row.get("metrics", {}).get("population_calibration_bins")
        if not isinstance(raw_bins, list):
            return None
        for raw in raw_bins:
            if not isinstance(raw, dict):
                return None
            low = raw.get("low")
            high = raw.get("high")
            count = raw.get("count")
            probability = raw.get("mean_probability")
            outcome = raw.get("mean_outcome")
            if not all(
                isinstance(value, (int, float))
                for value in (low, high, count, probability, outcome)
            ):
                return None
            assert isinstance(low, (int, float))
            assert isinstance(high, (int, float))
            assert isinstance(count, (int, float))
            assert isinstance(probability, (int, float))
            assert isinstance(outcome, (int, float))
            key = (float(low), float(high))
            target = bins.setdefault(
                key,
                {"count": 0.0, "probability_sum": 0.0, "outcome_sum": 0.0},
            )
            target["count"] += float(count)
            target["probability_sum"] += float(count) * float(probability)
            target["outcome_sum"] += float(count) * float(outcome)
    total = sum(row["count"] for row in bins.values())
    if not total:
        return None
    return sum(
        row["count"]
        / total
        * abs(
            row["probability_sum"] / row["count"]
            - row["outcome_sum"] / row["count"]
        )
        for row in bins.values()
        if row["count"]
    )


def _paired_bootstrap(
    observations: list[dict[str, Any]],
    *,
    run_id: str,
    resample_count: int = 10_000,
) -> dict[str, Any]:
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in observations:
        by_case.setdefault(str(row["case_id"]), {})[str(row["variant_id"])] = row
    paired = [rows for _case_id, rows in sorted(by_case.items()) if set(rows) >= {"V0", "V1"}]
    seed_text = f"{run_id}|paired-bootstrap|{resample_count}"
    seed_sha256 = sha256_text(seed_text)
    rng = random.Random(int(seed_sha256[:16], 16))
    result: dict[str, Any] = {
        "schema_version": "nslab.runtime_variant_paired_bootstrap.v2",
        "paired_case_count": len(paired),
        "resample_count": resample_count,
        "seed_sha256": seed_sha256,
        "metrics": {},
    }
    if not paired:
        return result
    metrics = (
        "recall_at_20",
        "precision_at_20",
        *(f"upper_limit_recall_at_{cutoff}" for cutoff in (5, 10, 20)),
        *(f"high20_recall_at_{cutoff}" for cutoff in (5, 10, 20)),
        *(f"high10_recall_at_{cutoff}" for cutoff in (5, 10, 20)),
        *(f"high20_precision_at_{cutoff}" for cutoff in (5, 10, 20)),
        "mrr",
        "leader_selection_accuracy",
        *(f"leader_recall_at_{cutoff}" for cutoff in (5, 10, 20)),
        *(f"max_return_tie_aware_hit_at_{cutoff}" for cutoff in (5, 10, 20)),
        "top_pick_high20_hit",
        "population_brier",
        "population_expected_calibration_error",
        "population_brier_skill_vs_climatology",
        "final_memory_citation_rate",
    )
    lower_is_better = {
        "population_brier",
        "population_expected_calibration_error",
    }
    for metric in metrics:
        metric_pairs = [
            (rows["V0"]["metrics"].get(metric), rows["V1"]["metrics"].get(metric))
            for rows in paired
        ]
        raw_deltas = [
            float(right) - float(left)
            for left, right in metric_pairs
            if isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        ]
        if not raw_deltas:
            result["metrics"][metric] = {
                "paired_observation_count": 0,
                "status": "UNAVAILABLE_NO_PAIRED_NUMERIC_OBSERVATIONS",
            }
            continue
        improvement_deltas = [
            -value if metric in lower_is_better else value for value in raw_deltas
        ]
        bootstrapped = sorted(
            sum(improvement_deltas[rng.randrange(len(improvement_deltas))] for _ in improvement_deltas)
            / len(improvement_deltas)
            for _ in range(resample_count)
        )
        result["metrics"][metric] = {
            "paired_observation_count": len(raw_deltas),
            "status": "AVAILABLE",
            "raw_v1_minus_v0": sum(raw_deltas) / len(raw_deltas),
            "improvement_direction_mean": sum(improvement_deltas) / len(improvement_deltas),
            "improvement_ci95_low": _percentile(bootstrapped, 0.025),
            "improvement_ci95_high": _percentile(bootstrapped, 0.975),
            "bootstrap_positive_share": sum(value > 0.0 for value in bootstrapped) / len(bootstrapped),
        }
    return result


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    index = min(len(values) - 1, max(0, int((len(values) - 1) * quantile + 0.999999)))
    return values[index]


def _render_report(report: dict[str, Any]) -> str:
    metrics = report["variant_metrics"]
    runtime = report["variant_runtime"]

    def value(section: dict[str, Any], key: str) -> str:
        observed = section.get(key)
        if isinstance(observed, float):
            return f"{observed:.6f}"
        return str(observed) if observed is not None else "unavailable"

    return "\n".join(
        [
            "# Runtime Variant Shadow",
            "",
            f"- Run: `{report['run_id']}`",
            f"- Scope: `{report['evaluation_scope']}`",
            f"- Paired cases: {report['paired_case_count']} / {report['expected_case_count']}",
            f"- Formal split cases: {report['formal_split_case_count']}",
            f"- Runtime gate: **{report['runtime_gate']}**",
            f"- V1 offline-unexposed recovered: {report['v1_offline_unexposed_recovered_count']}",
            f"- Future leak: {report['future_record_count']}",
            f"- BLIND web calls: {report['blind_web_call_count']}",
            f"- Online full scans: {report['online_full_scan_count']}",
            "- Known-relevant recall: unavailable until sealed relevance labels exist.",
            "- Rare recovery: audit-defined payload rarity proxy, not labeled recall.",
            "- Production activation: NOT_PRODUCTION_ACTIVATED",
            "",
            "## Market Metrics",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {value(metrics['V0'], key)} | {value(metrics['V1'], key)} |"
                for key in (
                    "micro_upper_limit_recall_at_20",
                    "micro_high20_recall_at_20",
                    "micro_high10_recall_at_20",
                    "micro_high20_precision_at_20",
                    "brier",
                    "expected_calibration_error",
                    "leader_high20_hit",
                    "final_memory_citation_rate",
                )
            ],
            "",
            "## Runtime Metrics",
            "",
            "| Metric | V0 | V1 |",
            "| --- | ---: | ---: |",
            *[
                f"| {key} | {value(runtime['V0'], key)} | {value(runtime['V1'], key)} |"
                for key in (
                    "offline_unexposed_candidate_count",
                    "offline_unexposed_recovered_count",
                    "offline_unexposed_llm_exposed_count",
                    "offline_unexposed_final_cited_count",
                    "oauth_live_agent_call_count",
                    "llm_prompt_tokens_estimate",
                    "llm_completion_tokens_estimate",
                    "embedding_query_count",
                    "p95_elapsed_seconds",
                    "process_peak_working_set_bytes",
                )
            ],
            "",
        ]
    )
