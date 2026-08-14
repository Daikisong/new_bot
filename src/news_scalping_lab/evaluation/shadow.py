"""Deterministic Phase 8 A-F shadow replay evaluation."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import duckdb

from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.config import load_settings
from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    MemoryCellSnapshotManifest,
    NumericDistribution,
)
from news_scalping_lab.contracts.models import BlindPrediction, BrainManifest, ContextManifest
from news_scalping_lab.contracts.shadow_evaluation import (
    SHADOW_ARM_IDS,
    ShadowArmAttestation,
    ShadowArmMetrics,
    ShadowArmObservation,
    ShadowBiasAudit,
    ShadowCalibrationBucket,
    ShadowDatasetAttestation,
    ShadowDatasetSplit,
    ShadowEvaluationManifest,
    ShadowExitGate,
    ShadowLoadAttestation,
    ShadowLoadProfile,
    ShadowRate,
    ShadowReplayCase,
    ShadowReplayDataset,
    ShadowSplitAttestation,
    ShadowTruthAttestation,
)
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.codex_oauth_provider import CodexOAuthProvider
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.openai_provider import OpenAIResponsesProvider
from news_scalping_lab.memory.index import (
    MEMORY_INDEX_ROOT,
    MEMORY_MANIFEST_FILE,
    MEMORY_SNAPSHOT_DIR,
    ProductionMemoryIndex,
    inspect_current_memory_index,
    inspect_memory_snapshot,
)
from news_scalping_lab.memory.runtime import create_production_memory_index
from news_scalping_lab.policies import EvidencePolicy
from news_scalping_lab.prices.base import OutcomeUniversePriceSource, PriceSource
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    now_kst,
    parse_datetime,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

SHADOW_EVALUATION_PROTOCOL_VERSION = "shadow_replay_ablation.v3"
SHADOW_METRIC_VERSION = "shadow_metrics.v1"
SHADOW_CALIBRATION_VERSION = "confidence_bucket_empirical.v1"
SHADOW_BIAS_AUDIT_VERSION = "shadow_bias_audit.v2"
SHADOW_SYSTEM_BUDGET_VERSION = "shadow_system_budget.v2"
SHADOW_ARTIFACT_ROOT = Path("runs/shadow_evaluation")
SHADOW_MANIFEST_FILE = "shadow_evaluation_manifest.json"
SHADOW_DATASET_FILE = "source_dataset.json"
SHADOW_CASE_RESULTS_FILE = "case_results.jsonl"
SHADOW_CALIBRATION_FILE = "calibration_buckets.jsonl"
SHADOW_SPLIT_ROOT = SHADOW_ARTIFACT_ROOT / "splits"
SHADOW_SPLIT_FILE = "split_manifest.json"
SHADOW_DATASET_ROOT = SHADOW_ARTIFACT_ROOT / "datasets"
SHADOW_ARM_OBSERVATION_ROOT = SHADOW_ARTIFACT_ROOT / "arm_observations"

SHADOW_MIN_CALIBRATION_CASES = 20
SHADOW_MIN_HOLDOUT_CASES = 20
SHADOW_PRE_LLM_P95_BUDGET_MS = 5_000.0
SHADOW_DAILY_P95_BUDGET_MS = 90_000.0
SHADOW_NORMAL_INPUT_TOKEN_P95 = 50_000.0
SHADOW_HARD_INPUT_TOKEN_MAX = 80_000.0
SHADOW_PEAK_MEMORY_BUDGET_BYTES = 8 * 1024 * 1024 * 1024
SHADOW_ESTIMATED_COST_PER_CASE_BUDGET_USD = 5.0
SHADOW_SPLIT_HMAC_KEY_ENV = "NSLAB_SHADOW_EVALUATION_HMAC_KEY"
SHADOW_RUNNER_HMAC_KEY_ENV = "NSLAB_SHADOW_RUNNER_HMAC_KEY"
SHADOW_TRUTH_HMAC_KEY_ENV = "NSLAB_SHADOW_TRUTH_HMAC_KEY"
SHADOW_SPLIT_MINIMUM_KEY_BYTES = 32

_CONFIDENCE_LABELS = ("very_high", "high", "medium", "low", "speculative")


@dataclass(frozen=True)
class ShadowEvaluationResult:
    manifest: ShadowEvaluationManifest
    manifest_path: Path


@dataclass(frozen=True)
class _ComputedEvaluation:
    case_rows: list[dict[str, Any]]
    calibration_buckets: list[ShadowCalibrationBucket]
    arm_metrics: list[ShadowArmMetrics]
    bias_audit: ShadowBiasAudit
    exit_gate: ShadowExitGate


def seal_shadow_split(
    root: Path,
    plan_path: Path,
    *,
    key_value: str | None = None,
) -> tuple[ShadowDatasetSplit, Path]:
    resolved_root = root.resolve()
    resolved_plan_path = plan_path.resolve()
    try:
        resolved_plan_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("shadow split plan escapes the project root") from exc
    payload = read_json(resolved_plan_path)
    if not isinstance(payload, dict):
        raise ValueError("shadow split plan must be a JSON object")
    expected_fields = {
        "schema_version",
        "build_start",
        "build_end",
        "calibration_start",
        "calibration_end",
        "holdout_start",
        "holdout_end",
        "calibration_dates",
        "holdout_dates",
    }
    if set(payload) != expected_fields or payload.get("schema_version") != (
        "nslab.shadow_dataset_split_plan.v1"
    ):
        raise ValueError("shadow split plan fields or schema version are invalid")
    effective_issued_at = as_kst(now_kst())
    split_fields = {
        key: value for key, value in payload.items() if key != "schema_version"
    }
    split_fields["sealed_at"] = effective_issued_at.isoformat()
    attestation = build_shadow_split_attestation(
        split_payload=shadow_split_commitment_payload(split_fields),
        key_value=key_value,
        issued_at=effective_issued_at,
    )
    placeholder = ArtifactReference(
        artifact_path="runs/shadow_evaluation/splits/pending/split_manifest.json",
        sha256="0" * 64,
        item_count=1,
    )
    split = ShadowDatasetSplit.model_validate(
        {
            **split_fields,
            "pre_registration_attestation": attestation.model_dump(mode="json"),
            "split_manifest": placeholder.model_dump(mode="json"),
        }
    )
    split_id = "SPLIT-" + attestation.commitment_sha256[:20].upper()
    path = resolved_root / SHADOW_SPLIT_ROOT / split_id / SHADOW_SPLIT_FILE
    source_payload = {
        "schema_version": "nslab.shadow_dataset_split.v1",
        **split.model_dump(mode="json", exclude={"split_manifest"}),
    }
    _write_immutable_json(path, source_payload)
    reference = ArtifactReference(
        artifact_path=relative_to_root(path, resolved_root),
        sha256=file_sha256(path),
        item_count=1,
    )
    sealed = split.model_copy(update={"split_manifest": reference})
    if not verify_shadow_split_attestation(
        sealed.pre_registration_attestation,
        split_payload=shadow_split_commitment_payload(sealed),
        key_value=key_value,
    ):
        raise ValueError("sealed shadow split failed attestation verification")
    return sealed, path


def seal_shadow_dataset(
    root: Path,
    unsigned_dataset_path: Path,
    *,
    key_value: str | None = None,
    memory_index: ProductionMemoryIndex | None = None,
    price_source: PriceSource | None = None,
    llm_provider: LLMProvider | None = None,
    runner_attestation_key: str | None = None,
    truth_attestation_key: str | None = None,
) -> tuple[ShadowReplayDataset, Path]:
    resolved_root = root.resolve()
    resolved_dataset_path = unsigned_dataset_path.resolve()
    try:
        resolved_dataset_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("unsigned shadow dataset escapes the project root") from exc
    payload = read_json(resolved_dataset_path)
    if not isinstance(payload, dict):
        raise ValueError("unsigned shadow dataset must be a JSON object")
    expected_fields = {
        "schema_version",
        "dataset_id",
        "dataset_kind",
        "split",
        "cases",
        "load_profiles",
    }
    if set(payload) != expected_fields or payload.get("schema_version") != (
        "nslab.shadow_replay_dataset_unsigned.v1"
    ):
        raise ValueError("unsigned shadow dataset fields or schema version are invalid")
    effective_issued_at = as_kst(now_kst())
    dataset_fields = {
        "schema_version": "nslab.shadow_replay_dataset.v1",
        "dataset_id": payload["dataset_id"],
        "dataset_kind": payload["dataset_kind"],
        "created_at": effective_issued_at.isoformat(),
        "split": payload["split"],
        "cases": payload["cases"],
        "load_profiles": payload["load_profiles"],
    }
    attestation = build_shadow_dataset_attestation(
        dataset_payload=shadow_dataset_commitment_payload(dataset_fields),
        key_value=key_value,
        issued_at=effective_issued_at,
    )
    dataset = ShadowReplayDataset.model_validate(
        {
            **dataset_fields,
            "dataset_attestation": attestation.model_dump(mode="json"),
        }
    )
    source_errors = ShadowReplayEvaluator(
        resolved_root,
        pre_registration_key=key_value,
        memory_index=memory_index,
        price_source=price_source,
        llm_provider=llm_provider,
        runner_attestation_key=runner_attestation_key,
        truth_attestation_key=truth_attestation_key,
    )._source_errors(dataset, verify_sealed_dataset=False)
    if source_errors:
        raise ValueError(
            "shadow replay source validation failed before sealing: "
            + ", ".join(source_errors)
        )
    dataset_id = "DATASET-" + attestation.commitment_sha256[:20].upper()
    path = resolved_root / SHADOW_DATASET_ROOT / dataset_id / SHADOW_DATASET_FILE
    _write_immutable_json(path, dataset.model_dump(mode="json"))
    if not verify_shadow_dataset_attestation(
        dataset.dataset_attestation,
        dataset_payload=shadow_dataset_commitment_payload(dataset),
        key_value=key_value,
    ):
        raise ValueError("sealed shadow dataset failed attestation verification")
    return dataset, path


class ShadowReplayEvaluator:
    """Build and independently inspect content-addressed shadow evaluations."""

    def __init__(
        self,
        root: Path,
        *,
        pre_registration_key: str | None = None,
        memory_index: ProductionMemoryIndex | None = None,
        price_source: PriceSource | None = None,
        llm_provider: LLMProvider | None = None,
        runner_attestation_key: str | None = None,
        truth_attestation_key: str | None = None,
    ) -> None:
        self.root = root.resolve()
        settings = load_settings(self.root)
        self.pre_registration_key = (
            pre_registration_key
            if pre_registration_key is not None
            else settings.env_value(SHADOW_SPLIT_HMAC_KEY_ENV)
        )
        self.memory_index = memory_index
        self.price_source = price_source
        self.llm_provider = llm_provider
        self.runner_attestation_key = (
            runner_attestation_key
            if runner_attestation_key is not None
            else settings.env_value(SHADOW_RUNNER_HMAC_KEY_ENV)
        )
        self.truth_attestation_key = (
            truth_attestation_key
            if truth_attestation_key is not None
            else settings.env_value(SHADOW_TRUTH_HMAC_KEY_ENV)
        )

    def evaluate(self, dataset_path: Path) -> ShadowEvaluationResult:
        source_path = dataset_path.resolve()
        dataset = ShadowReplayDataset.model_validate(read_json(source_path))
        expected_source_path = _sealed_dataset_path(self.root, dataset)
        if source_path != expected_source_path:
            raise ValueError("shadow replay dataset path is not canonical")
        source_errors = self._source_errors(dataset)
        if source_errors:
            raise ValueError("shadow replay source validation failed: " + ", ".join(source_errors))
        dataset_bytes = _json_bytes(dataset.model_dump(mode="json"))
        dataset_sha256 = sha256_text(dataset_bytes.decode("utf-8"))
        identity = {
            "dataset_id": dataset.dataset_id,
            "dataset_sha256": dataset_sha256,
            "protocol_version": SHADOW_EVALUATION_PROTOCOL_VERSION,
            "metric_version": SHADOW_METRIC_VERSION,
            "calibration_version": SHADOW_CALIBRATION_VERSION,
            "bias_audit_version": SHADOW_BIAS_AUDIT_VERSION,
            "system_budget_version": SHADOW_SYSTEM_BUDGET_VERSION,
        }
        evaluation_id = "SHADOW-" + sha256_text(canonical_json(identity))[:20].upper()
        artifact_dir = self.root / SHADOW_ARTIFACT_ROOT / evaluation_id
        manifest_path = artifact_dir / SHADOW_MANIFEST_FILE
        expected = self._build_manifest(
            dataset,
            dataset_bytes=dataset_bytes,
            dataset_sha256=dataset_sha256,
            evaluation_id=evaluation_id,
            artifact_dir=artifact_dir,
            source_closure_verified=(
                dataset.dataset_kind == "SEALED_HISTORICAL_REPLAY"
            ),
        )
        if manifest_path.exists():
            observed = ShadowEvaluationManifest.model_validate(read_json(manifest_path))
            if observed != expected:
                raise ValueError("existing shadow evaluation conflicts with deterministic content")
            inspection = self.inspect(manifest_path)
            if not inspection["passed"]:
                raise ValueError(
                    "existing shadow evaluation failed inspection: "
                    + ", ".join(inspection["errors"])
                )
            return ShadowEvaluationResult(manifest=observed, manifest_path=manifest_path)
        computed = _compute_evaluation(
            dataset,
            source_closure_verified=(
                dataset.dataset_kind == "SEALED_HISTORICAL_REPLAY"
            ),
        )
        case_bytes = _jsonl_bytes(computed.case_rows)
        bucket_bytes = _jsonl_bytes(
            [bucket.model_dump(mode="json") for bucket in computed.calibration_buckets]
        )
        _write_immutable_bytes(artifact_dir / SHADOW_DATASET_FILE, dataset_bytes)
        _write_immutable_bytes(artifact_dir / SHADOW_CASE_RESULTS_FILE, case_bytes)
        _write_immutable_bytes(artifact_dir / SHADOW_CALIBRATION_FILE, bucket_bytes)
        _write_immutable_json(manifest_path, expected.model_dump(mode="json"))
        inspection = self.inspect(manifest_path)
        if not inspection["passed"]:
            raise ValueError(
                "shadow evaluation failed self-inspection: "
                + ", ".join(inspection["errors"])
            )
        return ShadowEvaluationResult(manifest=expected, manifest_path=manifest_path)

    def inspect(self, manifest_path: Path) -> dict[str, Any]:
        path = manifest_path.resolve()
        base: dict[str, Any] = {
            "manifest_path": relative_to_root(path, self.root),
            "passed": False,
            "errors": [],
        }
        try:
            manifest = ShadowEvaluationManifest.model_validate(read_json(path))
        except (OSError, ValueError) as exc:
            return {**base, "errors": [f"shadow_manifest_invalid:{exc}"]}
        expected_path = (
            self.root
            / SHADOW_ARTIFACT_ROOT
            / manifest.evaluation_id
            / SHADOW_MANIFEST_FILE
        ).resolve()
        errors: list[str] = []
        if path != expected_path:
            errors.append("shadow_manifest_path_mismatch")
        artifacts: dict[str, bytes] = {}
        for label, ref, filename in (
            ("source_dataset", manifest.source_dataset, SHADOW_DATASET_FILE),
            ("case_results", manifest.case_results, SHADOW_CASE_RESULTS_FILE),
            ("calibration_buckets", manifest.calibration_buckets, SHADOW_CALIBRATION_FILE),
        ):
            artifact_path = (self.root / ref.artifact_path).resolve()
            if artifact_path != path.parent / filename:
                errors.append(f"shadow_{label}_path_mismatch")
                continue
            try:
                content = artifact_path.read_bytes()
            except OSError:
                errors.append(f"shadow_{label}_missing")
                continue
            artifacts[label] = content
            if file_sha256(artifact_path) != ref.sha256:
                errors.append(f"shadow_{label}_hash_mismatch")
            count = 1 if label == "source_dataset" else _jsonl_count(content)
            if count != ref.item_count:
                errors.append(f"shadow_{label}_count_mismatch")
        dataset_bytes = artifacts.get("source_dataset")
        if dataset_bytes is None:
            return {**base, "errors": sorted(set(errors))}
        try:
            dataset = ShadowReplayDataset.model_validate(json.loads(dataset_bytes))
        except (json.JSONDecodeError, ValueError) as exc:
            errors.append(f"shadow_source_dataset_invalid:{exc}")
            return {**base, "errors": sorted(set(errors))}
        source_errors = self._source_errors(dataset)
        errors.extend(source_errors)
        canonical_dataset_bytes = _json_bytes(dataset.model_dump(mode="json"))
        dataset_sha256 = sha256_text(canonical_dataset_bytes.decode("utf-8"))
        if dataset_bytes != canonical_dataset_bytes:
            errors.append("shadow_source_dataset_not_canonical")
        if dataset.dataset_id != manifest.dataset_id:
            errors.append("shadow_dataset_id_mismatch")
        if dataset_sha256 != manifest.dataset_sha256:
            errors.append("shadow_dataset_sha256_mismatch")
        identity = {
            "dataset_id": dataset.dataset_id,
            "dataset_sha256": dataset_sha256,
            "protocol_version": SHADOW_EVALUATION_PROTOCOL_VERSION,
            "metric_version": SHADOW_METRIC_VERSION,
            "calibration_version": SHADOW_CALIBRATION_VERSION,
            "bias_audit_version": SHADOW_BIAS_AUDIT_VERSION,
            "system_budget_version": SHADOW_SYSTEM_BUDGET_VERSION,
        }
        expected_id = "SHADOW-" + sha256_text(canonical_json(identity))[:20].upper()
        if manifest.evaluation_id != expected_id:
            errors.append("shadow_evaluation_id_mismatch")
        try:
            expected = self._build_manifest(
                dataset,
                dataset_bytes=canonical_dataset_bytes,
                dataset_sha256=dataset_sha256,
                evaluation_id=expected_id,
                artifact_dir=path.parent,
                source_closure_verified=(
                    dataset.dataset_kind == "SEALED_HISTORICAL_REPLAY"
                ),
            )
        except ValueError as exc:
            errors.append(f"shadow_recompute_failed:{exc}")
        else:
            if manifest != expected:
                errors.append("shadow_manifest_recomputed_mismatch")
            computed = _compute_evaluation(
                dataset,
                source_closure_verified=(
                    dataset.dataset_kind == "SEALED_HISTORICAL_REPLAY"
                ),
            )
            expected_case_bytes = _jsonl_bytes(computed.case_rows)
            expected_bucket_bytes = _jsonl_bytes(
                [
                    bucket.model_dump(mode="json")
                    for bucket in computed.calibration_buckets
                ]
            )
            if artifacts.get("case_results") != expected_case_bytes:
                errors.append("shadow_case_results_recomputed_mismatch")
            if artifacts.get("calibration_buckets") != expected_bucket_bytes:
                errors.append("shadow_calibration_recomputed_mismatch")
        return {
            **base,
            "passed": not errors,
            "errors": sorted(set(errors)),
            "evaluation_id": manifest.evaluation_id,
            "dataset_id": manifest.dataset_id,
            "production_ready": manifest.production_ready,
            "holdout_case_count": manifest.holdout_case_count,
        }

    def _build_manifest(
        self,
        dataset: ShadowReplayDataset,
        *,
        dataset_bytes: bytes,
        dataset_sha256: str,
        evaluation_id: str,
        artifact_dir: Path,
        source_closure_verified: bool,
    ) -> ShadowEvaluationManifest:
        computed = _compute_evaluation(
            dataset,
            source_closure_verified=source_closure_verified,
        )
        case_bytes = _jsonl_bytes(computed.case_rows)
        bucket_bytes = _jsonl_bytes(
            [bucket.model_dump(mode="json") for bucket in computed.calibration_buckets]
        )
        calibration_count = sum(case.split == "CALIBRATION" for case in dataset.cases)
        holdout_count = sum(case.split == "HOLDOUT" for case in dataset.cases)
        return ShadowEvaluationManifest(
            evaluation_id=evaluation_id,
            created_at=dataset.created_at,
            dataset_id=dataset.dataset_id,
            dataset_sha256=dataset_sha256,
            protocol_version=SHADOW_EVALUATION_PROTOCOL_VERSION,
            metric_version=SHADOW_METRIC_VERSION,
            calibration_version=SHADOW_CALIBRATION_VERSION,
            bias_audit_version=SHADOW_BIAS_AUDIT_VERSION,
            system_budget_version=SHADOW_SYSTEM_BUDGET_VERSION,
            calibration_case_count=calibration_count,
            holdout_case_count=holdout_count,
            arm_metrics=computed.arm_metrics,
            calibration_buckets=_artifact_reference(
                self.root,
                artifact_dir / SHADOW_CALIBRATION_FILE,
                bucket_bytes,
                item_count=len(computed.calibration_buckets),
            ),
            case_results=_artifact_reference(
                self.root,
                artifact_dir / SHADOW_CASE_RESULTS_FILE,
                case_bytes,
                item_count=len(computed.case_rows),
            ),
            source_dataset=_artifact_reference(
                self.root,
                artifact_dir / SHADOW_DATASET_FILE,
                dataset_bytes,
                item_count=1,
            ),
            bias_audit=computed.bias_audit,
            load_profiles=dataset.load_profiles,
            exit_gate=computed.exit_gate,
            production_ready=computed.exit_gate.passed,
        )

    def _source_errors(
        self,
        dataset: ShadowReplayDataset,
        *,
        verify_sealed_dataset: bool = True,
    ) -> list[str]:
        errors: list[str] = []
        if not verify_shadow_split_attestation(
            dataset.split.pre_registration_attestation,
            split_payload=shadow_split_commitment_payload(dataset.split),
            key_value=self.pre_registration_key,
        ):
            errors.append("shadow_split_pre_registration_attestation_invalid")
        if not verify_shadow_dataset_attestation(
            dataset.dataset_attestation,
            dataset_payload=shadow_dataset_commitment_payload(dataset),
            key_value=self.pre_registration_key,
        ):
            errors.append("shadow_dataset_attestation_invalid")
        if verify_sealed_dataset:
            sealed_dataset_path = _sealed_dataset_path(self.root, dataset)
            try:
                sealed_dataset_bytes = sealed_dataset_path.read_bytes()
            except OSError:
                errors.append("shadow_sealed_dataset_missing")
            else:
                expected_dataset_bytes = _json_bytes(dataset.model_dump(mode="json"))
                if sealed_dataset_bytes != expected_dataset_bytes:
                    errors.append("shadow_sealed_dataset_recomputed_mismatch")
        references: list[tuple[str, ArtifactReference]] = [
            ("split_manifest", dataset.split.split_manifest)
        ]
        for case in dataset.cases:
            if not verify_shadow_truth_attestation(
                case.truth_attestation,
                truth_payload=shadow_truth_commitment_payload(case),
                key_value=self.truth_attestation_key,
            ):
                errors.append(
                    f"shadow_truth_attestation_invalid:{case.case_id}"
                )
            references.extend(
                (
                    (f"{case.case_id}:news", case.news_artifact),
                    (f"{case.case_id}:truth", case.truth_artifact),
                )
            )
            if case.postmortem_artifact is not None:
                references.append(
                    (f"{case.case_id}:postmortem", case.postmortem_artifact)
                )
            for arm in case.arms:
                if not verify_shadow_arm_attestation(
                    arm.execution_attestation,
                    arm_payload=shadow_arm_commitment_payload(case, arm),
                    key_value=self.runner_attestation_key,
                ):
                    errors.append(
                        f"shadow_arm_attestation_invalid:{case.case_id}:{arm.arm_id}"
                    )
                references.extend(
                    (f"{case.case_id}:{arm.arm_id}:source", ref)
                    for ref in arm.source_artifacts
                )
                if arm.as_of_snapshot is not None:
                    references.append(
                        (
                            f"{case.case_id}:{arm.arm_id}:snapshot",
                            arm.as_of_snapshot.snapshot_manifest,
                        )
                    )
                    references.append(
                        (
                            f"{case.case_id}:{arm.arm_id}:brain",
                            arm.as_of_snapshot.brain_manifest,
                        )
                    )
        if dataset.dataset_kind == "SEALED_HISTORICAL_REPLAY":
            errors.extend(self._production_provider_errors(dataset))
            if self.memory_index is None:
                errors.append("shadow_historical_source_closure_memory_index_required")
            else:
                try:
                    lookahead = audit_lookahead(
                        self.root,
                        memory_index=self.memory_index,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    errors.append(f"shadow_historical_lookahead_audit_failed:{exc}")
                else:
                    expected_manifest_count = sum(
                        len(case.arms) for case in dataset.cases
                    )
                    checked = lookahead.get("checked_manifests")
                    if (
                        lookahead.get("passed") is not True
                        or not isinstance(checked, int)
                        or isinstance(checked, bool)
                        or checked < expected_manifest_count
                    ):
                        errors.append("shadow_historical_lookahead_audit_failed")
        for profile in dataset.load_profiles:
            if profile.measured and (
                profile.load_attestation is None
                or not verify_shadow_load_attestation(
                    profile.load_attestation,
                    load_payload=shadow_load_commitment_payload(profile),
                    key_value=self.runner_attestation_key,
                )
            ):
                errors.append(
                    f"shadow_load_attestation_invalid:{profile.record_count}"
                )
            if profile.workload_artifact is not None:
                references.append(
                    (
                        f"load:{profile.record_count}:workload",
                        profile.workload_artifact,
                    )
                )
            if profile.profile_artifact is not None:
                references.append((f"load:{profile.record_count}", profile.profile_artifact))
            if profile.source_snapshot_manifest is not None:
                references.append(
                    (
                        f"load:{profile.record_count}:snapshot",
                        profile.source_snapshot_manifest,
                    )
                )
            references.extend(
                (
                    f"load:{profile.record_count}:sample:{index}",
                    artifact,
                )
                for index, artifact in enumerate(profile.sample_artifacts)
            )
        seen: dict[str, tuple[str, int]] = {}
        for label, ref in references:
            path = _safe_artifact_path(self.root, ref.artifact_path)
            if path is None:
                errors.append(f"shadow_source_path_invalid:{label}")
                continue
            current = (ref.sha256, ref.item_count)
            previous = seen.get(ref.artifact_path)
            if previous is not None and previous != current:
                errors.append(f"shadow_source_reference_conflict:{label}")
                continue
            if previous == current:
                continue
            seen[ref.artifact_path] = current
            try:
                content = path.read_bytes()
            except OSError:
                errors.append(f"shadow_source_missing:{label}")
                continue
            if file_sha256(path) != ref.sha256:
                errors.append(f"shadow_source_hash_mismatch:{label}")
            observed_count = _jsonl_count(content) if path.suffix == ".jsonl" else 1
            if observed_count != ref.item_count:
                errors.append(f"shadow_source_count_mismatch:{label}")
        errors.extend(self._split_source_errors(dataset))
        snapshot_inspections: dict[str, dict[str, object]] = {}
        for case in dataset.cases:
            errors.extend(
                self._case_source_errors(
                    case,
                    snapshot_inspections=snapshot_inspections,
                )
            )
        for profile in dataset.load_profiles:
            if profile.profile_artifact is None:
                continue
            profile_path = _safe_artifact_path(
                self.root,
                profile.profile_artifact.artifact_path,
            )
            if profile_path is None or not profile_path.exists():
                continue
            expected_profile = {
                "schema_version": "nslab.shadow_load_profile.v1",
                **profile.model_dump(mode="json", exclude={"profile_artifact"}),
            }
            try:
                observed_profile = read_json(profile_path)
            except (OSError, ValueError):
                errors.append(f"shadow_load_profile_invalid:{profile.record_count}")
            else:
                if observed_profile != expected_profile:
                    errors.append(
                        f"shadow_load_profile_recomputed_mismatch:{profile.record_count}"
                    )
            errors.extend(self._load_profile_source_errors(profile))
        return sorted(set(errors))

    def _production_provider_errors(
        self,
        dataset: ShadowReplayDataset,
    ) -> list[str]:
        errors: list[str] = []
        provider = self.llm_provider
        if type(provider) not in {
            OpenAIResponsesProvider,
            CodexOAuthProvider,
        }:
            errors.append("shadow_real_llm_provider_required")
        else:
            expected_models = {
                arm.execution.llm_model
                for case in dataset.cases
                for arm in case.arms
            }
            if expected_models != {getattr(provider, "model", None)}:
                errors.append("shadow_llm_provider_model_mismatch")
        if type(self.price_source) is not StockWebPriceSource:
            errors.append("shadow_real_price_provider_required")
        return errors

    def _load_profile_source_errors(
        self,
        profile: ShadowLoadProfile,
    ) -> list[str]:
        if not profile.measured:
            return []
        label = str(profile.record_count)
        if profile.load_attestation is None or not verify_shadow_load_attestation(
            profile.load_attestation,
            load_payload=shadow_load_commitment_payload(profile),
            key_value=self.runner_attestation_key,
        ):
            return [f"shadow_load_attestation_invalid:{label}"]
        ref = profile.source_snapshot_manifest
        if ref is None or profile.source_snapshot_id is None:
            return [f"shadow_load_snapshot_reference_missing:{label}"]
        path = _safe_artifact_path(self.root, ref.artifact_path)
        if path is None:
            return [f"shadow_load_snapshot_path_invalid:{label}"]
        expected_path = (
            self.root
            / MEMORY_INDEX_ROOT
            / MEMORY_SNAPSHOT_DIR
            / profile.source_snapshot_id
            / MEMORY_MANIFEST_FILE
        ).resolve()
        if path != expected_path:
            return [f"shadow_load_snapshot_path_mismatch:{label}"]
        try:
            manifest = MemoryCellSnapshotManifest.model_validate(read_json(path))
        except (OSError, ValueError):
            return [f"shadow_load_snapshot_contract_invalid:{label}"]
        errors: list[str] = []
        if (
            manifest.snapshot_id != profile.source_snapshot_id
            or manifest.record_count != profile.record_count
            or manifest.source_generation_sha256 != profile.source_generation_sha256
            or manifest.corpus_manifest_sha256 != profile.corpus_manifest_sha256
            or manifest.embedding_provider != profile.embedding_provider
            or manifest.embedding_model != profile.embedding_model
            or manifest.embedding_dimensions != profile.embedding_dimensions
            or manifest.real_embedding is not profile.real_embedding_provider
        ):
            errors.append(f"shadow_load_snapshot_projection_mismatch:{label}")
        if not manifest.production_ready:
            errors.append(f"shadow_load_snapshot_not_ready:{label}")
        inspection = inspect_memory_snapshot(self.root, manifest.snapshot_id)
        if (
            inspection.get("passed") is not True
            or inspection.get("production_ready") is not True
        ):
            errors.append(f"shadow_load_snapshot_inspection_failed:{label}")
        if profile.workload_sha256 is None:
            errors.append(f"shadow_load_workload_identity_missing:{label}")
            return errors
        profile_path = _safe_artifact_path(
            self.root,
            profile.profile_artifact.artifact_path
            if profile.profile_artifact is not None
            else "",
        )
        expected_profile_path = _shadow_load_profile_path(self.root, profile)
        if profile_path != expected_profile_path:
            errors.append(f"shadow_load_profile_path_mismatch:{label}")
        workload_ref = profile.workload_artifact
        if workload_ref is None:
            errors.append(f"shadow_load_workload_reference_missing:{label}")
        else:
            workload_path = _safe_artifact_path(
                self.root,
                workload_ref.artifact_path,
            )
            expected_workload_path = expected_profile_path.parent / "workload.json"
            if workload_path != expected_workload_path:
                errors.append(f"shadow_load_workload_path_mismatch:{label}")
            else:
                try:
                    workload = read_json(workload_path)
                except (OSError, ValueError):
                    workload = None
                expected_workload = {
                    "schema_version": "nslab.shadow_load_workload.v1",
                    "profiler_version": profile.profiler_version,
                    "record_count": profile.record_count,
                    "source_snapshot_id": manifest.snapshot_id,
                    "embedding_provider": manifest.embedding_provider,
                    "embedding_model": manifest.embedding_model,
                    "embedding_dimensions": manifest.embedding_dimensions,
                    "operations": [
                        "pre_llm_retrieval",
                        "daily_analysis",
                    ],
                    "sample_count": len(profile.sample_run_ids),
                }
                if (
                    workload != expected_workload
                    or sha256_text(canonical_json(expected_workload))
                    != profile.workload_sha256
                ):
                    errors.append(f"shadow_load_workload_recomputed_mismatch:{label}")
        for index, artifact in enumerate(profile.sample_artifacts):
            sample_path = _safe_artifact_path(self.root, artifact.artifact_path)
            expected_sample_path = expected_profile_path.parent / (
                f"{profile.sample_run_ids[index]}.json"
            )
            if sample_path != expected_sample_path:
                errors.append(f"shadow_load_sample_path_mismatch:{label}:{index}")
                continue
            expected_sample = {
                "schema_version": "nslab.shadow_load_sample.v1",
                "record_count": profile.record_count,
                "source_snapshot_id": manifest.snapshot_id,
                "workload_sha256": profile.workload_sha256,
                "run_id": profile.sample_run_ids[index],
                "started_at": as_kst(profile.sample_started_at[index]).isoformat(),
                "completed_at": as_kst(profile.sample_completed_at[index]).isoformat(),
                "pre_llm_latency_ms": profile.pre_llm_latency_samples_ms[index],
                "daily_analysis_latency_ms": (
                    profile.daily_analysis_latency_samples_ms[index]
                ),
                "peak_memory_bytes": profile.peak_memory_samples_bytes[index],
                "online_full_scan_count": profile.online_full_scan_samples[index],
            }
            try:
                observed_sample = read_json(sample_path)
            except (OSError, ValueError):
                observed_sample = None
            if observed_sample != expected_sample:
                errors.append(
                    f"shadow_load_sample_recomputed_mismatch:{label}:{index}"
                )
        return errors

    def _split_source_errors(self, dataset: ShadowReplayDataset) -> list[str]:
        path = _safe_artifact_path(
            self.root,
            dataset.split.split_manifest.artifact_path,
        )
        if path is None or not path.exists():
            return []
        expected_path = _sealed_split_path(self.root, dataset.split)
        if path != expected_path:
            return ["shadow_split_manifest_path_mismatch"]
        expected = {
            "schema_version": "nslab.shadow_dataset_split.v1",
            **dataset.split.model_dump(mode="json", exclude={"split_manifest"}),
        }
        try:
            observed = read_json(path)
        except (OSError, ValueError):
            return ["shadow_split_manifest_invalid"]
        return [] if observed == expected else ["shadow_split_manifest_recomputed_mismatch"]

    def _case_source_errors(
        self,
        case: ShadowReplayCase,
        *,
        snapshot_inspections: dict[str, dict[str, object]],
    ) -> list[str]:
        errors: list[str] = []
        historical = (
            case.arms[0].execution.execution_mode
            == "SEALED_HISTORICAL_REPLAY"
        )
        if historical:
            errors.extend(self._historical_case_source_errors(case))
        truth_path = _safe_artifact_path(self.root, case.truth_artifact.artifact_path)
        if truth_path is not None and truth_path.exists():
            if truth_path != _shadow_truth_path(self.root, case):
                errors.append(f"shadow_truth_path_mismatch:{case.case_id}")
            expected_truth = {
                "schema_version": "nslab.shadow_truth.v1",
                "case_id": case.case_id,
                "trade_date": case.trade_date.isoformat(),
                "outcomes": [outcome.model_dump(mode="json") for outcome in case.outcomes],
                "known_relevant_record_ids": case.known_relevant_record_ids,
                "negative_control_record_ids": case.negative_control_record_ids,
                "counterexample_record_ids": case.counterexample_record_ids,
                "long_tail_beneficiary_tickers": case.long_tail_beneficiary_tickers,
                "truth_attestation": case.truth_attestation.model_dump(mode="json"),
            }
            try:
                observed_truth = read_json(truth_path)
            except (OSError, ValueError):
                errors.append(f"shadow_truth_invalid:{case.case_id}")
            else:
                if observed_truth != expected_truth:
                    errors.append(f"shadow_truth_recomputed_mismatch:{case.case_id}")
        for arm in case.arms:
            if not verify_shadow_arm_attestation(
                arm.execution_attestation,
                arm_payload=shadow_arm_commitment_payload(case, arm),
                key_value=self.runner_attestation_key,
            ):
                errors.append(
                    f"shadow_arm_attestation_invalid:{case.case_id}:{arm.arm_id}"
                )
            source_path = _safe_artifact_path(
                self.root,
                arm.source_artifacts[0].artifact_path,
            )
            if source_path is not None and source_path.exists():
                expected_source = shadow_arm_source_payload(case, arm)
                expected_source_path = _shadow_arm_observation_path(
                    self.root,
                    case,
                    arm,
                )
                if source_path != expected_source_path:
                    errors.append(
                        f"shadow_arm_source_path_mismatch:{case.case_id}:{arm.arm_id}"
                    )
                try:
                    observed_source = read_json(source_path)
                except (OSError, ValueError):
                    errors.append(f"shadow_arm_source_invalid:{case.case_id}:{arm.arm_id}")
                else:
                    if observed_source != expected_source:
                        errors.append(
                            f"shadow_arm_source_recomputed_mismatch:{case.case_id}:{arm.arm_id}"
                        )
            if historical:
                errors.extend(self._historical_arm_source_errors(case, arm))
            snapshot = arm.as_of_snapshot
            if snapshot is not None:
                snapshot_path = _safe_artifact_path(
                    self.root,
                    snapshot.snapshot_manifest.artifact_path,
                )
                if snapshot_path is not None and snapshot_path.exists():
                    try:
                        payload = read_json(snapshot_path)
                    except (OSError, ValueError):
                        errors.append(
                            f"shadow_snapshot_manifest_invalid:{case.case_id}:{arm.arm_id}"
                        )
                    else:
                        if not _snapshot_payload_matches(snapshot, payload):
                            errors.append(
                                "shadow_snapshot_manifest_recomputed_mismatch:"
                                f"{case.case_id}:{arm.arm_id}"
                            )
                        elif snapshot.snapshot_kind == "LEGACY_TOP3_INDEX":
                            errors.extend(
                                _legacy_snapshot_errors(
                                    self.root,
                                    case=case,
                                    arm=arm,
                                    payload=payload,
                                )
                            )
                        elif snapshot.snapshot_kind == "PRODUCTION_MEMORY_CELLS":
                            expected_path = (
                                self.root
                                / MEMORY_INDEX_ROOT
                                / MEMORY_SNAPSHOT_DIR
                                / snapshot.snapshot_id
                                / MEMORY_MANIFEST_FILE
                            ).resolve()
                            if snapshot_path != expected_path:
                                errors.append(
                                    "shadow_production_snapshot_path_mismatch:"
                                    f"{case.case_id}:{arm.arm_id}"
                                )
                                continue
                            try:
                                production_manifest = (
                                    MemoryCellSnapshotManifest.model_validate(payload)
                                )
                            except ValueError:
                                errors.append(
                                    "shadow_production_snapshot_contract_invalid:"
                                    f"{case.case_id}:{arm.arm_id}"
                                )
                                continue
                            if not production_manifest.production_ready:
                                errors.append(
                                    "shadow_production_snapshot_not_ready:"
                                    f"{case.case_id}:{arm.arm_id}"
                                )
                                continue
                            inspection = snapshot_inspections.get(snapshot.snapshot_id)
                            if inspection is None:
                                inspection = inspect_memory_snapshot(
                                    self.root,
                                    snapshot.snapshot_id,
                                )
                                snapshot_inspections[snapshot.snapshot_id] = inspection
                            if (
                                inspection.get("passed") is not True
                                or inspection.get("production_ready") is not True
                            ):
                                errors.append(
                                    "shadow_production_snapshot_inspection_failed:"
                                    f"{case.case_id}:{arm.arm_id}"
                                )
                            else:
                                errors.extend(
                                    _production_retrieved_record_errors(
                                        self.root,
                                        case=case,
                                        arm=arm,
                                        manifest=production_manifest,
                                    )
                                )
                        errors.extend(
                            _brain_snapshot_errors(
                                self.root,
                                case=case,
                                arm=arm,
                                payload_path=snapshot.brain_manifest,
                            )
                        )
        return errors

    def _historical_case_source_errors(
        self,
        case: ShadowReplayCase,
    ) -> list[str]:
        errors: list[str] = []
        if not verify_shadow_truth_attestation(
            case.truth_attestation,
            truth_payload=shadow_truth_commitment_payload(case),
            key_value=self.truth_attestation_key,
        ):
            errors.append(f"shadow_truth_attestation_invalid:{case.case_id}")
        path = _safe_artifact_path(self.root, case.news_artifact.artifact_path)
        if path is None:
            return [f"shadow_news_path_invalid:{case.case_id}"]
        try:
            batch = load_news_csv(path, trade_date=case.trade_date)
        except (OSError, ValueError):
            return [f"shadow_news_contract_invalid:{case.case_id}"]
        if not batch.items:
            errors.append(f"shadow_news_empty:{case.case_id}")
        if any(
            as_kst(item.published_at) > as_kst(case.replay_cutoff_at)
            for item in batch.items
        ):
            errors.append(f"shadow_news_cutoff_after_row:{case.case_id}")
        if not isinstance(self.price_source, OutcomeUniversePriceSource):
            errors.append(f"shadow_price_universe_provider_required:{case.case_id}")
        else:
            try:
                universe = self.price_source.get_outcome_universe(
                    trade_date=case.trade_date
                )
            except (OSError, RuntimeError, ValueError):
                errors.append(f"shadow_price_universe_query_failed:{case.case_id}")
            else:
                expected = {outcome.ticker: outcome for outcome in case.outcomes}
                if set(universe) != set(expected):
                    errors.append(f"shadow_price_universe_tickers_mismatch:{case.case_id}")
                else:
                    for ticker, truth in expected.items():
                        observed = universe[ticker]
                        if (
                            observed.intraday_high_return_pct is None
                            or observed.close_return_pct is None
                            or observed.upper_limit_touched is None
                            or float(observed.intraday_high_return_pct)
                            != truth.high_return_pct
                            or float(observed.close_return_pct)
                            != truth.close_return_pct
                            or bool(observed.upper_limit_touched)
                            is not truth.upper_limit_touched
                            or truth.candidate_relevant
                            is not (
                                truth.upper_limit_touched
                                or truth.high_return_pct >= 5.0
                            )
                        ):
                            errors.append(
                                f"shadow_price_truth_projection_mismatch:{case.case_id}:{ticker}"
                            )
        postmortem_ref = case.postmortem_artifact
        if postmortem_ref is None:
            errors.append(f"shadow_postmortem_reference_missing:{case.case_id}")
        else:
            postmortem_path = _safe_artifact_path(
                self.root,
                postmortem_ref.artifact_path,
            )
            if postmortem_path is None:
                errors.append(f"shadow_postmortem_path_invalid:{case.case_id}")
            else:
                allowed_postmortem_paths = {
                    (
                        self.root
                        / "reports"
                        / f"{case.trade_date.isoformat()}_postmortem.json"
                    ).resolve()
                }
                for arm in case.arms:
                    context_path = _safe_artifact_path(
                        self.root,
                        arm.source_artifacts[2].artifact_path,
                    )
                    if context_path is not None:
                        allowed_postmortem_paths.add(
                            (
                                self.root
                                / "runs"
                                / "checkpoints"
                                / "evaluations"
                                / context_path.stem
                                / "postmortem_report.json"
                            ).resolve()
                        )
                if postmortem_path not in allowed_postmortem_paths:
                    errors.append(
                        f"shadow_postmortem_path_mismatch:{case.case_id}"
                    )
                try:
                    postmortem = read_json(postmortem_path)
                except (OSError, ValueError):
                    postmortem = None
                if (
                    not isinstance(postmortem, dict)
                    or postmortem.get("schema_version") != "nslab.evaluation.v1"
                    or postmortem.get("trade_date") != case.trade_date.isoformat()
                    or postmortem.get("outcome_coverage_status")
                    != "FULL_MARKET_COMPLETE"
                    or postmortem.get("shadow_truth_sha256")
                    != case.truth_artifact.sha256
                    or postmortem.get("shadow_retrieval_truth")
                    != {
                        "schema_version": "nslab.shadow_retrieval_truth.v1",
                        "known_relevant_record_ids": (
                            case.known_relevant_record_ids
                        ),
                        "negative_control_record_ids": (
                            case.negative_control_record_ids
                        ),
                        "counterexample_record_ids": (
                            case.counterexample_record_ids
                        ),
                        "long_tail_beneficiary_tickers": (
                            case.long_tail_beneficiary_tickers
                        ),
                    }
                    or postmortem.get("shadow_candidate_truth")
                    != {
                        "schema_version": "nslab.shadow_candidate_truth.v1",
                        "outcomes": [
                            {
                                "ticker": outcome.ticker,
                                "actual_theme_id": outcome.actual_theme_id,
                                "is_theme_leader": outcome.is_theme_leader,
                                "newsless": outcome.newsless,
                            }
                            for outcome in case.outcomes
                        ],
                    }
                ):
                    errors.append(f"shadow_postmortem_contract_invalid:{case.case_id}")
        return errors

    def _historical_arm_source_errors(
        self,
        case: ShadowReplayCase,
        arm: ShadowArmObservation,
    ) -> list[str]:
        label = f"{case.case_id}:{arm.arm_id}"
        if len(arm.source_artifacts) != 3:
            return [f"shadow_historical_source_count_mismatch:{label}"]
        prediction_path = _safe_artifact_path(
            self.root,
            arm.source_artifacts[1].artifact_path,
        )
        context_path = _safe_artifact_path(
            self.root,
            arm.source_artifacts[2].artifact_path,
        )
        if prediction_path is None or context_path is None:
            return [f"shadow_historical_source_path_invalid:{label}"]
        try:
            prediction = BlindPrediction.model_validate(read_json(prediction_path))
            context = ContextManifest.model_validate(read_json(context_path))
        except (OSError, ValueError):
            return [f"shadow_historical_source_contract_invalid:{label}"]
        errors: list[str] = []
        expected_context_path = (
            self.root / "runs" / "manifests" / f"{context.run_id}.json"
        ).resolve()
        expected_prediction_path = (
            self.root
            / "runs"
            / "checkpoints"
            / "output_artifacts"
            / context.run_id
            / "blind_prediction.json"
        ).resolve()
        if (
            context_path != expected_context_path
            or prediction_path != expected_prediction_path
            or prediction.context_manifest_id != context.run_id
        ):
            errors.append(f"shadow_historical_source_canonical_path_mismatch:{label}")
        if (
            prediction.prediction_id != arm.prediction_id
            or prediction.trade_date != case.trade_date
            or as_kst(prediction.cutoff_at) != as_kst(case.replay_cutoff_at)
        ):
            errors.append(f"shadow_prediction_identity_mismatch:{label}")
        observed_candidates = [
            (
                item.rank,
                item.ticker,
                item.company_name,
                str(item.confidence_label),
                item.memory_record_ids,
                item.claimed_theme_id,
                item.claims_news_cause,
            )
            for item in prediction.candidates
        ]
        expected_candidates = [
            (
                item.rank,
                item.ticker,
                item.company_name,
                item.confidence_label,
                item.memory_record_ids,
                item.claimed_theme_id,
                item.claims_news_cause,
            )
            for item in arm.candidates
        ]
        if observed_candidates != expected_candidates:
            errors.append(f"shadow_prediction_candidate_projection_mismatch:{label}")
        if (
            context.trade_date != case.trade_date
            or as_kst(context.cutoff_at) != as_kst(case.replay_cutoff_at)
            or context.prediction_artifact != arm.source_artifacts[1].artifact_path
            or context.prediction_sha256 != arm.source_artifacts[1].sha256
            or context.news_file != case.news_artifact.artifact_path
            or context.news_sha256 != case.news_artifact.sha256
            or context.no_d_outcome_exposed is not True
            or context.blind_current_price_access_count != 0
            or context.errors
            or context.evidence_policy != "csv-memory-only-strict"
            or context.web_provider != "disabled"
            or context.web_required is not False
            or context.blind_web_search_call_count != 0
            or context.external_web_evidence_count != 0
        ):
            errors.append(f"shadow_context_identity_mismatch:{label}")
        expected_config = {
            "arm_id": arm.arm_id,
            "features": arm.features.model_dump(mode="json"),
            "runner_protocol_version": arm.execution.runner_protocol_version,
            "llm_provider": arm.execution.llm_provider,
            "llm_model": arm.execution.llm_model,
            "prompt_version": arm.execution.prompt_version,
            "inference_config_sha256": arm.execution.inference_config_sha256,
        }
        if context.llm_model_config.get("shadow_replay") != expected_config:
            errors.append(f"shadow_context_ablation_config_mismatch:{label}")
        configured_provider = str(
            context.llm_model_config.get("configured_provider", "")
        ).strip()
        configured_model = str(context.llm_model_config.get("model", "")).strip()
        provider_class = str(
            context.llm_model_config.get("provider_class", "")
        ).strip()
        if (
            configured_provider != arm.execution.llm_provider
            or configured_model != arm.execution.llm_model
            or provider_class
            not in {
                OpenAIResponsesProvider.__name__,
                CodexOAuthProvider.__name__,
            }
            or configured_provider.lower() in {"mock", "deterministic", "test"}
        ):
            errors.append(f"shadow_context_provider_attestation_mismatch:{label}")
        if context.retrieved_record_ids != [
            record.record_id for record in arm.retrieved_records
        ]:
            errors.append(f"shadow_context_retrieved_record_ids_mismatch:{label}")
        return errors


def shadow_replay_readiness(root: Path) -> dict[str, Any]:
    resolved_root = root.resolve()
    prediction_paths = list(
        (resolved_root / "predictions").glob("????-??-??.json")
    ) + list(
        (resolved_root / "runs" / "checkpoints" / "output_artifacts").glob(
            "*/blind_prediction.json"
        )
    )
    postmortem_paths = list(
        (resolved_root / "reports").glob("????-??-??_postmortem.json")
    ) + list(
        (resolved_root / "runs" / "checkpoints" / "evaluations").glob(
            "*/postmortem_report.json"
        )
    )
    prediction_dates = _artifact_trade_dates(prediction_paths)
    postmortem_dates = _artifact_trade_dates(postmortem_paths)
    paired_dates = sorted(prediction_dates.intersection(postmortem_dates))
    memory_status = inspect_current_memory_index(resolved_root)
    brain_path = resolved_root / "brain" / "current" / "brain_manifest.json"
    try:
        brain = read_json(brain_path)
    except (OSError, ValueError):
        brain = {}
    if not isinstance(brain, dict):
        brain = {}
    settings = load_settings(resolved_root)
    configured_key = settings.env_value(SHADOW_SPLIT_HMAC_KEY_ENV)
    production_evaluation_ids: list[str] = []
    if (
        configured_key
        and settings.llm_provider.strip().lower() not in {"", "mock"}
        and settings.price_provider.strip().lower() not in {"", "mock"}
        and memory_status.get("production_ready") is True
    ):
        try:
            evaluator = ShadowReplayEvaluator(
                resolved_root,
                pre_registration_key=configured_key,
                memory_index=create_production_memory_index(
                    settings,
                    require_records=False,
                ),
                price_source=create_price_source(settings),
                llm_provider=create_llm_provider(settings),
                runner_attestation_key=settings.env_value(
                    SHADOW_RUNNER_HMAC_KEY_ENV
                ),
                truth_attestation_key=settings.env_value(
                    SHADOW_TRUTH_HMAC_KEY_ENV
                ),
            )
        except (OSError, RuntimeError, ValueError):
            evaluator = None
        if evaluator is not None:
            production_evaluation_ids = _production_shadow_evaluation_ids(
                resolved_root,
                evaluator=evaluator,
            )
    checks = {
        "minimum_calibration_and_holdout_days": len(paired_dates)
        >= SHADOW_MIN_CALIBRATION_CASES + SHADOW_MIN_HOLDOUT_CASES,
        "production_memory_snapshot_ready": memory_status.get("production_ready")
        is True,
        "llm_full_production_brain": (
            brain.get("build_mode") == "llm-full"
            and brain.get("production_eligible") is True
        ),
        "real_llm_provider_configured": settings.llm_provider.strip().lower()
        not in {"", "mock"},
        "real_price_provider_configured": settings.price_provider.strip().lower()
        not in {"", "mock"},
        "csv_memory_only_evidence_policy": (
            settings.evidence_policy is EvidencePolicy.CSV_MEMORY_ONLY_STRICT
        ),
        "web_disabled_by_design": (
            settings.web_provider.strip().lower() == "disabled"
        ),
        "real_web_provider_configured": (
            settings.evidence_policy is EvidencePolicy.CSV_MEMORY_ONLY_STRICT
            and settings.web_provider.strip().lower() == "disabled"
        )
        or settings.web_provider.strip().lower() not in {"", "mock", "disabled"},
        "shadow_pre_registration_key_configured": bool(
            settings.env_value(SHADOW_SPLIT_HMAC_KEY_ENV)
        ),
        "shadow_runner_attestation_key_configured": bool(
            settings.env_value(SHADOW_RUNNER_HMAC_KEY_ENV)
        ),
        "shadow_truth_attestation_key_configured": bool(
            settings.env_value(SHADOW_TRUTH_HMAC_KEY_ENV)
        ),
        "actual_a_to_f_source_closure_available": bool(production_evaluation_ids),
    }
    blockers = sorted(name for name, passed in checks.items() if not passed)
    return {
        "schema_version": "nslab.shadow_replay_readiness.v1",
        "prediction_date_count": len(prediction_dates),
        "postmortem_date_count": len(postmortem_dates),
        "paired_historical_day_count": len(paired_dates),
        "paired_historical_dates": paired_dates,
        "memory_snapshot_id": memory_status.get("snapshot_id"),
        "memory_status": memory_status.get("status"),
        "brain_version": brain.get("brain_version"),
        "brain_build_mode": brain.get("build_mode"),
        "production_shadow_evaluation_ids": production_evaluation_ids,
        "checks": checks,
        "ready": not blockers,
        "blockers": blockers,
    }


def _production_shadow_evaluation_ids(
    root: Path,
    *,
    evaluator: ShadowReplayEvaluator,
) -> list[str]:
    values: list[str] = []
    for path in sorted((root / SHADOW_ARTIFACT_ROOT).glob("SHADOW-*/shadow_evaluation_manifest.json")):
        try:
            manifest = ShadowEvaluationManifest.model_validate(read_json(path))
        except (OSError, ValueError):
            continue
        expected = (
            root
            / SHADOW_ARTIFACT_ROOT
            / manifest.evaluation_id
            / SHADOW_MANIFEST_FILE
        ).resolve()
        inspection = evaluator.inspect(path)
        if (
            path.resolve() == expected
            and manifest.production_ready
            and inspection.get("passed") is True
            and inspection.get("production_ready") is True
        ):
            values.append(manifest.evaluation_id)
    return values


def _artifact_trade_dates(paths: Iterable[Path]) -> set[str]:
    values: set[str] = set()
    for path in paths:
        if not path.is_file():
            continue
        try:
            payload = read_json(path)
        except (OSError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        value = payload.get("trade_date")
        if not isinstance(value, str):
            continue
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            continue
        if parsed.isoformat() == value:
            values.add(value)
    return values


def shadow_split_commitment_payload(split: Any) -> dict[str, Any]:
    if hasattr(split, "model_dump"):
        payload = split.model_dump(
            mode="json",
            exclude={"pre_registration_attestation", "split_manifest"},
        )
    elif isinstance(split, dict):
        payload = {
            key: value
            for key, value in split.items()
            if key not in {"pre_registration_attestation", "split_manifest"}
        }
    else:
        raise ValueError("shadow split commitment payload must be a mapping")
    return {
        "schema_version": "nslab.shadow_split_commitment.v1",
        "split": payload,
    }


def shadow_dataset_commitment_payload(dataset: Any) -> dict[str, Any]:
    if hasattr(dataset, "model_dump"):
        payload = dataset.model_dump(mode="json", exclude={"dataset_attestation"})
    elif isinstance(dataset, dict):
        payload = {
            key: value for key, value in dataset.items() if key != "dataset_attestation"
        }
    else:
        raise ValueError("shadow dataset commitment payload must be a mapping")
    return {
        "schema_version": "nslab.shadow_dataset_commitment.v1",
        "dataset": payload,
    }


def shadow_arm_commitment_payload(
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.shadow_arm_commitment.v1",
        "case_id": case.case_id,
        "trade_date": case.trade_date.isoformat(),
        "replay_cutoff_at": as_kst(case.replay_cutoff_at).isoformat(),
        "arm": arm.model_dump(
            mode="json",
            exclude={"execution_attestation", "source_artifacts"},
        ),
    }


def shadow_truth_commitment_payload(case: ShadowReplayCase) -> dict[str, Any]:
    return {
        "schema_version": "nslab.shadow_truth_commitment.v1",
        "case_id": case.case_id,
        "trade_date": case.trade_date.isoformat(),
        "replay_cutoff_at": as_kst(case.replay_cutoff_at).isoformat(),
        "outcome_universe_complete": case.outcome_universe_complete,
        "outcomes": [outcome.model_dump(mode="json") for outcome in case.outcomes],
        "known_relevant_record_ids": case.known_relevant_record_ids,
        "negative_control_record_ids": case.negative_control_record_ids,
        "counterexample_record_ids": case.counterexample_record_ids,
        "long_tail_beneficiary_tickers": case.long_tail_beneficiary_tickers,
    }


def shadow_load_commitment_payload(profile: ShadowLoadProfile) -> dict[str, Any]:
    return {
        "schema_version": "nslab.shadow_load_commitment.v1",
        "profile": profile.model_dump(
            mode="json",
            exclude={"load_attestation", "profile_artifact"},
        ),
    }


def seal_shadow_arm_observation(
    root: Path,
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
    *,
    key_value: str | None = None,
) -> tuple[ShadowArmObservation, ArtifactReference]:
    issued_at = as_kst(now_kst())
    attestation = _build_shadow_arm_attestation(
        arm_payload=shadow_arm_commitment_payload(case, arm),
        key_value=key_value,
        issued_at=issued_at,
    )
    sealed = ShadowArmObservation.model_validate(
        {
            **arm.model_dump(mode="json"),
            "execution_attestation": attestation.model_dump(mode="json"),
        }
    )
    path = _shadow_arm_observation_path(root.resolve(), case, sealed)
    _write_immutable_json(path, shadow_arm_source_payload(case, sealed))
    return sealed, ArtifactReference(
        artifact_path=relative_to_root(path, root.resolve()),
        sha256=file_sha256(path),
        item_count=1,
    )


def seal_shadow_case_truth(
    root: Path,
    case: ShadowReplayCase,
    *,
    key_value: str | None = None,
) -> tuple[ShadowReplayCase, ArtifactReference]:
    issued_at = as_kst(now_kst())
    attestation = _build_shadow_truth_attestation(
        truth_payload=shadow_truth_commitment_payload(case),
        key_value=key_value,
        issued_at=issued_at,
    )
    sealed = ShadowReplayCase.model_validate(
        {
            **case.model_dump(mode="json"),
            "truth_attestation": attestation.model_dump(mode="json"),
        }
    )
    path = _shadow_truth_path(root.resolve(), sealed)
    payload = {
        "schema_version": "nslab.shadow_truth.v1",
        "case_id": sealed.case_id,
        "trade_date": sealed.trade_date.isoformat(),
        "outcomes": [outcome.model_dump(mode="json") for outcome in sealed.outcomes],
        "known_relevant_record_ids": sealed.known_relevant_record_ids,
        "negative_control_record_ids": sealed.negative_control_record_ids,
        "counterexample_record_ids": sealed.counterexample_record_ids,
        "long_tail_beneficiary_tickers": sealed.long_tail_beneficiary_tickers,
        "truth_attestation": attestation.model_dump(mode="json"),
    }
    _write_immutable_json(path, payload)
    reference = ArtifactReference(
        artifact_path=relative_to_root(path, root.resolve()),
        sha256=file_sha256(path),
        item_count=1,
    )
    return sealed.model_copy(update={"truth_artifact": reference}), reference


def seal_shadow_load_profile(
    root: Path,
    profile: ShadowLoadProfile,
    *,
    key_value: str | None = None,
) -> tuple[ShadowLoadProfile, ArtifactReference]:
    if not profile.measured:
        raise ValueError("only measured load profiles can be attested")
    issued_at = as_kst(now_kst())
    attestation = _build_shadow_load_attestation(
        load_payload=shadow_load_commitment_payload(profile),
        key_value=key_value,
        issued_at=issued_at,
    )
    sealed = ShadowLoadProfile.model_validate(
        {
            **profile.model_dump(mode="json"),
            "load_attestation": attestation.model_dump(mode="json"),
        }
    )
    path = _shadow_load_profile_path(root.resolve(), sealed)
    payload = {
        "schema_version": "nslab.shadow_load_profile.v1",
        **sealed.model_dump(mode="json", exclude={"profile_artifact"}),
    }
    _write_immutable_json(path, payload)
    reference = ArtifactReference(
        artifact_path=relative_to_root(path, root.resolve()),
        sha256=file_sha256(path),
        item_count=1,
    )
    return sealed.model_copy(update={"profile_artifact": reference}), reference


def build_shadow_split_attestation(
    *,
    split_payload: dict[str, Any],
    key_value: str | None = None,
    issued_at: datetime | None = None,
) -> ShadowSplitAttestation:
    key = _shadow_split_key(key_value)
    effective_issued_at = as_kst(issued_at or now_kst())
    commitment = canonical_json(
        {
            "issued_at": effective_issued_at.isoformat(),
            "split_payload": split_payload,
        }
    )
    return ShadowSplitAttestation(
        issued_at=effective_issued_at,
        key_id=sha256_text(key.hex())[:16],
        commitment_sha256=sha256_text(commitment),
        signature=hmac.new(
            key,
            commitment.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    )


def verify_shadow_split_attestation(
    attestation: ShadowSplitAttestation,
    *,
    split_payload: dict[str, Any],
    key_value: str | None = None,
) -> bool:
    try:
        expected = build_shadow_split_attestation(
            split_payload=split_payload,
            key_value=key_value,
            issued_at=attestation.issued_at,
        )
    except ValueError:
        return False
    return all(
        hmac.compare_digest(str(getattr(attestation, field)), str(getattr(expected, field)))
        for field in (
            "schema_version",
            "algorithm",
            "issued_at",
            "key_id",
            "commitment_sha256",
            "signature",
        )
    )


def build_shadow_dataset_attestation(
    *,
    dataset_payload: dict[str, Any],
    key_value: str | None = None,
    issued_at: datetime | None = None,
) -> ShadowDatasetAttestation:
    key = _shadow_split_key(key_value)
    effective_issued_at = as_kst(issued_at or now_kst())
    commitment = canonical_json(
        {
            "issued_at": effective_issued_at.isoformat(),
            "dataset_payload": dataset_payload,
        }
    )
    return ShadowDatasetAttestation(
        issued_at=effective_issued_at,
        key_id=sha256_text(key.hex())[:16],
        commitment_sha256=sha256_text(commitment),
        signature=hmac.new(
            key,
            commitment.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    )


def verify_shadow_dataset_attestation(
    attestation: ShadowDatasetAttestation,
    *,
    dataset_payload: dict[str, Any],
    key_value: str | None = None,
) -> bool:
    try:
        expected = build_shadow_dataset_attestation(
            dataset_payload=dataset_payload,
            key_value=key_value,
            issued_at=attestation.issued_at,
        )
    except ValueError:
        return False
    return all(
        hmac.compare_digest(str(getattr(attestation, field)), str(getattr(expected, field)))
        for field in (
            "schema_version",
            "algorithm",
            "issued_at",
            "key_id",
            "commitment_sha256",
            "signature",
        )
    )


def _build_shadow_arm_attestation(
    *,
    arm_payload: dict[str, Any],
    key_value: str | None,
    issued_at: datetime,
) -> ShadowArmAttestation:
    key = _shadow_hmac_key(key_value, SHADOW_RUNNER_HMAC_KEY_ENV)
    effective_issued_at = as_kst(issued_at)
    commitment = canonical_json(
        {
            "issued_at": effective_issued_at.isoformat(),
            "arm_payload": arm_payload,
        }
    )
    return ShadowArmAttestation(
        issued_at=effective_issued_at,
        key_id=sha256_text(key.hex())[:16],
        commitment_sha256=sha256_text(commitment),
        signature=hmac.new(
            key,
            commitment.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    )


def verify_shadow_arm_attestation(
    attestation: ShadowArmAttestation,
    *,
    arm_payload: dict[str, Any],
    key_value: str | None = None,
) -> bool:
    try:
        expected = _build_shadow_arm_attestation(
            arm_payload=arm_payload,
            key_value=key_value,
            issued_at=attestation.issued_at,
        )
    except ValueError:
        return False
    return _attestations_match(attestation, expected)


def _build_shadow_truth_attestation(
    *,
    truth_payload: dict[str, Any],
    key_value: str | None,
    issued_at: datetime,
) -> ShadowTruthAttestation:
    key = _shadow_hmac_key(key_value, SHADOW_TRUTH_HMAC_KEY_ENV)
    effective_issued_at = as_kst(issued_at)
    commitment = canonical_json(
        {
            "issued_at": effective_issued_at.isoformat(),
            "truth_payload": truth_payload,
        }
    )
    return ShadowTruthAttestation(
        issued_at=effective_issued_at,
        key_id=sha256_text(key.hex())[:16],
        commitment_sha256=sha256_text(commitment),
        signature=hmac.new(
            key,
            commitment.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    )


def verify_shadow_truth_attestation(
    attestation: ShadowTruthAttestation,
    *,
    truth_payload: dict[str, Any],
    key_value: str | None = None,
) -> bool:
    try:
        expected = _build_shadow_truth_attestation(
            truth_payload=truth_payload,
            key_value=key_value,
            issued_at=attestation.issued_at,
        )
    except ValueError:
        return False
    return _attestations_match(attestation, expected)


def _build_shadow_load_attestation(
    *,
    load_payload: dict[str, Any],
    key_value: str | None,
    issued_at: datetime,
) -> ShadowLoadAttestation:
    key = _shadow_hmac_key(key_value, SHADOW_RUNNER_HMAC_KEY_ENV)
    effective_issued_at = as_kst(issued_at)
    commitment = canonical_json(
        {
            "issued_at": effective_issued_at.isoformat(),
            "load_payload": load_payload,
        }
    )
    return ShadowLoadAttestation(
        issued_at=effective_issued_at,
        key_id=sha256_text(key.hex())[:16],
        commitment_sha256=sha256_text(commitment),
        signature=hmac.new(
            key,
            commitment.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    )


def verify_shadow_load_attestation(
    attestation: ShadowLoadAttestation,
    *,
    load_payload: dict[str, Any],
    key_value: str | None = None,
) -> bool:
    try:
        expected = _build_shadow_load_attestation(
            load_payload=load_payload,
            key_value=key_value,
            issued_at=attestation.issued_at,
        )
    except ValueError:
        return False
    return _attestations_match(attestation, expected)


def _attestations_match(observed: Any, expected: Any) -> bool:
    return all(
        hmac.compare_digest(
            str(getattr(observed, field)),
            str(getattr(expected, field)),
        )
        for field in (
            "schema_version",
            "algorithm",
            "issued_at",
            "key_id",
            "commitment_sha256",
            "signature",
        )
    )


def _shadow_split_key(key_value: str | None) -> bytes:
    return _shadow_hmac_key(key_value, SHADOW_SPLIT_HMAC_KEY_ENV)


def _shadow_hmac_key(key_value: str | None, environment_name: str) -> bytes:
    value = key_value if key_value is not None else os.environ.get(environment_name, "")
    key = value.encode("utf-8")
    if len(key) < SHADOW_SPLIT_MINIMUM_KEY_BYTES:
        raise ValueError(
            f"{environment_name} must contain at least "
            f"{SHADOW_SPLIT_MINIMUM_KEY_BYTES} UTF-8 bytes"
        )
    return key


def shadow_arm_source_payload(
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
) -> dict[str, Any]:
    observation = arm.model_dump(mode="json", exclude={"source_artifacts"})
    return {
        "schema_version": "nslab.shadow_arm_observation.v1",
        "case_id": case.case_id,
        "trade_date": case.trade_date.isoformat(),
        "replay_cutoff_at": as_kst(case.replay_cutoff_at).isoformat(),
        "arm_id": arm.arm_id,
        "observation_sha256": sha256_text(canonical_json(observation)),
        "observation": observation,
    }


def _shadow_arm_observation_path(
    root: Path,
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
) -> Path:
    payload = shadow_arm_source_payload(case, arm)
    return (
        root
        / SHADOW_ARM_OBSERVATION_ROOT
        / case.case_id
        / arm.arm_id
        / f"{payload['observation_sha256']}.json"
    ).resolve()


def _shadow_truth_path(root: Path, case: ShadowReplayCase) -> Path:
    return (
        root
        / SHADOW_ARTIFACT_ROOT
        / "truth"
        / case.case_id
        / f"{case.truth_attestation.commitment_sha256}.json"
    ).resolve()


def _shadow_load_profile_path(root: Path, profile: ShadowLoadProfile) -> Path:
    if profile.source_snapshot_id is None or profile.workload_sha256 is None:
        raise ValueError("measured load profile identity is incomplete")
    return (
        root
        / SHADOW_ARTIFACT_ROOT
        / "load_profiles"
        / str(profile.record_count)
        / profile.source_snapshot_id
        / profile.workload_sha256
        / "profile.json"
    ).resolve()


def _compute_evaluation(
    dataset: ShadowReplayDataset,
    *,
    source_closure_verified: bool,
) -> _ComputedEvaluation:
    calibration_cases = [case for case in dataset.cases if case.split == "CALIBRATION"]
    holdout_cases = [case for case in dataset.cases if case.split == "HOLDOUT"]
    calibration_rates = _calibration_rates(calibration_cases)
    buckets = _calibration_buckets(calibration_cases, holdout_cases, calibration_rates)
    case_rows: list[dict[str, Any]] = []
    arm_metrics: list[ShadowArmMetrics] = []
    for arm_id in SHADOW_ARM_IDS:
        rows = [
            _case_arm_row(case, _arm(case, arm_id), calibration_rates[arm_id])
            for case in holdout_cases
        ]
        case_rows.extend(rows)
        arm_buckets = [bucket for bucket in buckets if bucket.arm_id == arm_id]
        arm_metrics.append(_aggregate_arm_metrics(arm_id, rows, arm_buckets))
    case_rows.sort(key=lambda row: (row["trade_date"], row["arm_id"]))
    bias_audit = _bias_audit(dataset)
    exit_gate = _exit_gate(
        arm_metrics,
        load_profiles=dataset.load_profiles,
        bias_audit=bias_audit,
        calibration_case_count=len(calibration_cases),
        holdout_case_count=len(holdout_cases),
        historical_replay_verified=(
            dataset.dataset_kind == "SEALED_HISTORICAL_REPLAY"
        ),
        source_closure_verified=source_closure_verified,
    )
    return _ComputedEvaluation(
        case_rows=case_rows,
        calibration_buckets=buckets,
        arm_metrics=arm_metrics,
        bias_audit=bias_audit,
        exit_gate=exit_gate,
    )


def _calibration_rates(
    cases: list[ShadowReplayCase],
) -> dict[str, dict[str, float | None]]:
    counts: dict[str, dict[str, list[int]]] = {
        arm_id: {label: [0, 0] for label in _CONFIDENCE_LABELS}
        for arm_id in SHADOW_ARM_IDS
    }
    for case in cases:
        truth = {outcome.ticker: outcome for outcome in case.outcomes}
        for arm in case.arms:
            for candidate in arm.candidates:
                bucket = counts[arm.arm_id][candidate.confidence_label]
                bucket[1] += 1
                bucket[0] += int(truth[candidate.ticker].high_return_pct >= 10.0)
    return {
        arm_id: {
            label: None if denominator == 0 else numerator / denominator
            for label, (numerator, denominator) in labels.items()
        }
        for arm_id, labels in counts.items()
    }


def _calibration_buckets(
    calibration_cases: list[ShadowReplayCase],
    holdout_cases: list[ShadowReplayCase],
    rates: dict[str, dict[str, float | None]],
) -> list[ShadowCalibrationBucket]:
    output: list[ShadowCalibrationBucket] = []
    for arm_id in SHADOW_ARM_IDS:
        for label in _CONFIDENCE_LABELS:
            calibration_count = sum(
                candidate.confidence_label == label
                for case in calibration_cases
                for candidate in _arm(case, arm_id).candidates
            )
            holdout_values = [
                outcome.high_return_pct >= 10.0
                for case in holdout_cases
                for candidate in _arm(case, arm_id).candidates
                if candidate.confidence_label == label
                for outcome in case.outcomes
                if outcome.ticker == candidate.ticker
            ]
            output.append(
                ShadowCalibrationBucket(
                    arm_id=arm_id,
                    confidence_label=label,
                    calibration_observation_count=calibration_count,
                    calibrated_probability=rates[arm_id][label],
                    holdout_observation_count=len(holdout_values),
                    holdout_positive_count=sum(holdout_values),
                )
            )
    return output


def _case_arm_row(
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
    calibration_rates: dict[str, float | None],
) -> dict[str, Any]:
    truth = {outcome.ticker: outcome for outcome in case.outcomes}
    ranked = [candidate.ticker for candidate in arm.candidates]
    relevant = {outcome.ticker for outcome in case.outcomes if outcome.candidate_relevant}
    high_10 = {outcome.ticker for outcome in case.outcomes if outcome.high_return_pct >= 10.0}
    high_20 = {outcome.ticker for outcome in case.outcomes if outcome.high_return_pct >= 20.0}
    retrieved_ids = {record.record_id for record in arm.retrieved_records}
    retrieved_tickers = {
        record.ticker for record in arm.retrieved_records if record.ticker is not None
    }
    false_positive_count = sum(
        not truth[candidate.ticker].candidate_relevant for candidate in arm.candidates
    )
    theme_claims = [
        candidate for candidate in arm.candidates if candidate.claimed_theme_id is not None
    ]
    theme_over_expansion_count = sum(
        truth[candidate.ticker].actual_theme_id != candidate.claimed_theme_id
        for candidate in theme_claims
    )
    leader_denominator = 0
    leader_errors = 0
    leaders = {
        outcome.actual_theme_id: outcome.ticker
        for outcome in case.outcomes
        if outcome.is_theme_leader and outcome.actual_theme_id is not None
    }
    for theme_id, leader_ticker in leaders.items():
        leader_denominator += 1
        predicted = next(
            (
                candidate.ticker
                for candidate in arm.candidates
                if candidate.claimed_theme_id == theme_id
            ),
            None,
        )
        leader_errors += int(predicted != leader_ticker)
    newsless_selected = [
        candidate for candidate in arm.candidates if truth[candidate.ticker].newsless
    ]
    newsless_hallucinations = sum(
        candidate.claims_news_cause for candidate in newsless_selected
    )
    brier_terms: list[float] = []
    calibration_labels: list[str] = []
    calibrated_candidates: dict[str, float] = {}
    brier_complete = True
    for candidate in arm.candidates:
        probability = calibration_rates[candidate.confidence_label]
        if probability is None:
            brier_complete = False
            continue
        calibrated_candidates[candidate.ticker] = probability
        calibration_labels.append(candidate.confidence_label)
    if brier_complete:
        brier_terms = [
            (
                calibrated_candidates.get(outcome.ticker, 0.0)
                - float(outcome.high_return_pct >= 10.0)
            )
            ** 2
            for outcome in case.outcomes
        ]
    unit_count = len(arm.retrieved_records)
    unique_units = len({record.independent_unit_id for record in arm.retrieved_records})
    telemetry = arm.telemetry
    return {
        "schema_version": "nslab.shadow_case_arm_result.v1",
        "case_id": case.case_id,
        "trade_date": case.trade_date.isoformat(),
        "arm_id": arm.arm_id,
        "candidate_count": len(arm.candidates),
        "candidate_recall_5_numerator": len(set(ranked[:5]).intersection(relevant)),
        "candidate_recall_10_numerator": len(set(ranked[:10]).intersection(relevant)),
        "candidate_recall_20_numerator": len(set(ranked[:20]).intersection(relevant)),
        "candidate_recall_denominator": len(relevant),
        "high_10_numerator": len(set(ranked[:20]).intersection(high_10)),
        "high_10_denominator": len(high_10),
        "high_20_numerator": len(set(ranked[:20]).intersection(high_20)),
        "high_20_denominator": len(high_20),
        "false_positive_numerator": false_positive_count,
        "false_positive_denominator": len(arm.candidates),
        "leader_error_numerator": leader_errors,
        "leader_error_denominator": leader_denominator,
        "theme_over_expansion_numerator": theme_over_expansion_count,
        "theme_over_expansion_denominator": len(theme_claims),
        "newsless_hallucination_numerator": newsless_hallucinations,
        "newsless_hallucination_denominator": len(newsless_selected),
        "known_relevant_numerator": len(
            retrieved_ids.intersection(case.known_relevant_record_ids)
        ),
        "known_relevant_denominator": len(case.known_relevant_record_ids),
        "negative_control_numerator": len(
            retrieved_ids.intersection(case.negative_control_record_ids)
        ),
        "negative_control_denominator": len(case.negative_control_record_ids),
        "counterexample_numerator": len(
            retrieved_ids.intersection(case.counterexample_record_ids)
        ),
        "counterexample_denominator": len(case.counterexample_record_ids),
        "long_tail_numerator": len(
            retrieved_tickers.intersection(case.long_tail_beneficiary_tickers)
        ),
        "long_tail_denominator": len(case.long_tail_beneficiary_tickers),
        "retrieved_record_count": unit_count,
        "retrieved_unique_unit_count": unique_units,
        "retrieved_years": sorted({record.trade_date.year for record in arm.retrieved_records}),
        "retrieved_regimes": sorted(
            {record.regime_cluster for record in arm.retrieved_records}
        ),
        "brier_terms": brier_terms,
        "brier_complete": brier_complete,
        "calibration_labels": calibration_labels,
        "telemetry": telemetry.model_dump(mode="json"),
    }


def _aggregate_arm_metrics(
    arm_id: str,
    rows: list[dict[str, Any]],
    buckets: list[ShadowCalibrationBucket],
) -> ShadowArmMetrics:
    def rate(numerator: str, denominator: str) -> ShadowRate:
        return _rate(
            sum(int(row[numerator]) for row in rows),
            sum(int(row[denominator]) for row in rows),
        )

    brier_complete = all(bool(row["brier_complete"]) for row in rows)
    all_brier = [float(value) for row in rows for value in row["brier_terms"]]
    covered = sum(len(row["calibration_labels"]) for row in rows)
    candidate_count = sum(int(row["candidate_count"]) for row in rows)
    ece_numerator = 0.0
    for bucket in buckets:
        if (
            bucket.calibrated_probability is None
            or bucket.holdout_observation_count == 0
        ):
            continue
        observed_rate = (
            bucket.holdout_positive_count / bucket.holdout_observation_count
        )
        ece_numerator += (
            abs(observed_rate - bucket.calibrated_probability)
            * bucket.holdout_observation_count
        )
    telemetry = [row["telemetry"] for row in rows]
    record_count = sum(int(row["retrieved_record_count"]) for row in rows)
    unique_count = sum(int(row["retrieved_unique_unit_count"]) for row in rows)
    return ShadowArmMetrics(
        arm_id=arm_id,
        holdout_case_count=len(rows),
        candidate_recall_at_5=rate(
            "candidate_recall_5_numerator", "candidate_recall_denominator"
        ),
        candidate_recall_at_10=rate(
            "candidate_recall_10_numerator", "candidate_recall_denominator"
        ),
        candidate_recall_at_20=rate(
            "candidate_recall_20_numerator", "candidate_recall_denominator"
        ),
        high_10_recall=rate("high_10_numerator", "high_10_denominator"),
        high_20_recall=rate("high_20_numerator", "high_20_denominator"),
        false_positive_rate=rate(
            "false_positive_numerator", "false_positive_denominator"
        ),
        leader_error_rate=rate("leader_error_numerator", "leader_error_denominator"),
        theme_over_expansion_rate=rate(
            "theme_over_expansion_numerator", "theme_over_expansion_denominator"
        ),
        newsless_hallucination_rate=rate(
            "newsless_hallucination_numerator",
            "newsless_hallucination_denominator",
        ),
        known_relevant_record_recall=rate(
            "known_relevant_numerator", "known_relevant_denominator"
        ),
        negative_control_inclusion_rate=rate(
            "negative_control_numerator", "negative_control_denominator"
        ),
        counterexample_inclusion_rate=rate(
            "counterexample_numerator", "counterexample_denominator"
        ),
        long_tail_beneficiary_recall=rate(
            "long_tail_numerator", "long_tail_denominator"
        ),
        issuer_day_duplicate_rate=(
            0.0 if record_count == 0 else (record_count - unique_count) / record_count
        ),
        unique_year_count=len(
            {int(year) for row in rows for year in row["retrieved_years"]}
        ),
        unique_regime_count=len(
            {str(value) for row in rows for value in row["retrieved_regimes"]}
        ),
        brier_score=(
            None
            if not brier_complete or not all_brier
            else sum(all_brier) / len(all_brier)
        ),
        expected_calibration_error=(
            None if covered == 0 else ece_numerator / covered
        ),
        calibration_coverage=_rate(covered, candidate_count),
        pre_llm_latency_ms=_distribution(
            float(value["pre_llm_latency_ms"]) for value in telemetry
        ),
        daily_analysis_latency_ms=_distribution(
            float(value["daily_analysis_latency_ms"]) for value in telemetry
        ),
        llm_input_tokens=_distribution(
            float(value["llm_input_tokens"]) for value in telemetry
        ),
        llm_output_tokens=_distribution(
            float(value["llm_output_tokens"]) for value in telemetry
        ),
        embedding_query_count=_distribution(
            float(value["embedding_query_count"]) for value in telemetry
        ),
        cache_hit_rate=_rate(
            sum(int(value["cache_hit_count"]) for value in telemetry),
            sum(int(value["cache_lookup_count"]) for value in telemetry),
        ),
        peak_memory_bytes=max(
            (int(value["peak_memory_bytes"]) for value in telemetry),
            default=0,
        ),
        estimated_cost_usd=sum(
            float(value["estimated_cost_usd"]) for value in telemetry
        ),
        online_full_scan_count=sum(
            int(value["online_full_scan_count"]) for value in telemetry
        ),
    )


def _bias_audit(dataset: ShadowReplayDataset) -> ShadowBiasAudit:
    calibration_dates = sorted(
        case.trade_date for case in dataset.cases if case.split == "CALIBRATION"
    )
    holdout_dates = sorted(
        case.trade_date for case in dataset.cases if case.split == "HOLDOUT"
    )
    checks = {
        "sealed_date_plan_exact": (
            calibration_dates == dataset.split.calibration_dates
            and holdout_dates == dataset.split.holdout_dates
        ),
        "complete_outcome_universe": all(
            case.outcome_universe_complete for case in dataset.cases
        ),
        "all_arms_per_date": all(
            [arm.arm_id for arm in case.arms] == list(SHADOW_ARM_IDS)
            for case in dataset.cases
        ),
        "immutable_as_of_snapshots": all(
            arm.arm_id == "A"
            or (
                arm.as_of_snapshot is not None
                and arm.as_of_snapshot.immutable
                and as_kst(arm.as_of_snapshot.as_of_cutoff)
                <= as_kst(case.replay_cutoff_at)
            )
            for case in dataset.cases
            for arm in case.arms
        ),
        "no_cutoff_after_memory": all(
            as_kst(record.available_from) <= as_kst(case.replay_cutoff_at)
            and record.trade_date <= case.trade_date
            for case in dataset.cases
            for arm in case.arms
            for record in arm.retrieved_records
        ),
        "candidate_outcome_parity": all(
            {candidate.ticker for arm in case.arms for candidate in arm.candidates}
            <= {outcome.ticker for outcome in case.outcomes}
            for case in dataset.cases
        ),
        "arm_execution_contract_parity": all(
            len(
                {
                    (
                        arm.execution.execution_mode,
                        arm.execution.runner_protocol_version,
                        arm.execution.llm_provider,
                        arm.execution.llm_model,
                        arm.execution.prompt_version,
                        arm.execution.inference_config_sha256,
                        arm.execution.production_provider_attested,
                    )
                    for arm in case.arms
                }
            )
            == 1
            for case in dataset.cases
        ),
        "production_snapshot_parity": all(
            len(
                {
                    canonical_json(arm.as_of_snapshot.model_dump(mode="json"))
                    for arm in case.arms
                    if arm.arm_id in {"C", "D", "E", "F"}
                    and arm.as_of_snapshot is not None
                }
            )
            == 1
            for case in dataset.cases
        ),
    }
    errors = sorted(name for name, passed in checks.items() if not passed)
    return ShadowBiasAudit(checks=checks, passed=not errors, errors=errors)


def _exit_gate(
    metrics: list[ShadowArmMetrics],
    *,
    load_profiles: list[ShadowLoadProfile],
    bias_audit: ShadowBiasAudit,
    calibration_case_count: int,
    holdout_case_count: int,
    historical_replay_verified: bool,
    source_closure_verified: bool,
) -> ShadowExitGate:
    by_arm = {metric.arm_id: metric for metric in metrics}
    baseline = by_arm["B"]
    candidates = (by_arm["E"], by_arm["F"])

    def value(rate: ShadowRate, default: float) -> float:
        return default if rate.value is None else rate.value

    baseline_recall = value(baseline.candidate_recall_at_20, 0.0)
    baseline_brier = baseline.brier_score
    jointly_improved_arms = [
        candidate
        for candidate in candidates
        if value(candidate.candidate_recall_at_20, 0.0) > baseline_recall
        and baseline_brier is not None
        and candidate.brier_score is not None
        and candidate.brier_score < baseline_brier
        and candidate.calibration_coverage.value == 1.0
    ]
    baseline_newsless = value(baseline.newsless_hallucination_rate, 0.0)
    no_newsless_regression = all(
        value(candidate.newsless_hallucination_rate, 0.0) <= baseline_newsless
        for candidate in candidates
    )
    runtime_budget = all(
        candidate.pre_llm_latency_ms.p95 <= SHADOW_PRE_LLM_P95_BUDGET_MS
        and candidate.daily_analysis_latency_ms.p95
        <= SHADOW_DAILY_P95_BUDGET_MS
        and candidate.llm_input_tokens.p95 <= SHADOW_NORMAL_INPUT_TOKEN_P95
        and candidate.llm_input_tokens.maximum <= SHADOW_HARD_INPUT_TOKEN_MAX
        and candidate.online_full_scan_count == 0
        and candidate.peak_memory_bytes <= SHADOW_PEAK_MEMORY_BUDGET_BYTES
        and candidate.estimated_cost_usd / max(candidate.holdout_case_count, 1)
        <= SHADOW_ESTIMATED_COST_PER_CASE_BUDGET_USD
        for candidate in candidates
    )
    load_budget = all(
        profile.measured
        and profile.production_shape
        and profile.real_embedding_provider
        and profile.embedding_dimensions >= 1536
        and profile.pre_llm_latency_ms.p95 <= SHADOW_PRE_LLM_P95_BUDGET_MS
        and profile.daily_analysis_latency_ms.p95 <= SHADOW_DAILY_P95_BUDGET_MS
        and profile.online_full_scan_count == 0
        and profile.peak_memory_bytes <= SHADOW_PEAK_MEMORY_BUDGET_BYTES
        for profile in load_profiles
    )
    checks = {
        "sealed_historical_replay_execution": historical_replay_verified,
        "actual_a_to_f_source_closure_verified": source_closure_verified,
        "minimum_calibration_cases": calibration_case_count
        >= SHADOW_MIN_CALIBRATION_CASES,
        "minimum_holdout_cases": holdout_case_count >= SHADOW_MIN_HOLDOUT_CASES,
        "one_e_or_f_arm_jointly_improves_recall_and_calibration": bool(
            jointly_improved_arms
        ),
        "newsless_hallucination_not_worse": no_newsless_regression,
        "runtime_latency_token_scan_budget": runtime_budget,
        "production_load_profiles_50k_200k_600k": load_budget,
        "selection_and_survivorship_audit": bias_audit.passed,
    }
    blockers = sorted(name for name, passed in checks.items() if not passed)
    return ShadowExitGate(checks=checks, passed=not blockers, blockers=blockers)


def _arm(case: ShadowReplayCase, arm_id: str) -> ShadowArmObservation:
    return next(arm for arm in case.arms if arm.arm_id == arm_id)


def _rate(numerator: int, denominator: int) -> ShadowRate:
    return ShadowRate(
        numerator=numerator,
        denominator=denominator,
        value=None if denominator == 0 else numerator / denominator,
    )


def _distribution(values: Iterable[float]) -> NumericDistribution:
    ordered = sorted(float(value) for value in values)
    if any(not math.isfinite(value) or value < 0.0 for value in ordered):
        raise ValueError("shadow metric distributions require finite non-negative values")
    if not ordered:
        return NumericDistribution(
            count=0,
            minimum=0.0,
            mean=0.0,
            p50=0.0,
            p95=0.0,
            p99=0.0,
            maximum=0.0,
        )
    return NumericDistribution(
        count=len(ordered),
        minimum=ordered[0],
        mean=sum(ordered) / len(ordered),
        p50=_percentile(ordered, 0.50),
        p95=_percentile(ordered, 0.95),
        p99=_percentile(ordered, 0.99),
        maximum=ordered[-1],
    )


def _percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = (len(values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] * (1.0 - fraction) + values[upper] * fraction


def _snapshot_payload_matches(snapshot: Any, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if snapshot.snapshot_kind == "LEGACY_TOP3_INDEX" and payload.get(
        "schema_version"
    ) != "nslab.shadow_legacy_top3_snapshot.v1":
        return False
    expected = {
        "snapshot_id": snapshot.snapshot_id,
        "corpus_manifest_sha256": snapshot.corpus_manifest_sha256,
        "source_generation_sha256": snapshot.source_generation_sha256,
        "embedding_model": snapshot.embedding_model,
        "clustering_version": snapshot.clustering_version,
        "normalizer_version": snapshot.normalizer_version,
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        return False
    cutoff = payload.get("as_of_cutoff")
    if not isinstance(cutoff, str):
        return False
    try:
        return as_kst(parse_datetime(cutoff)) == as_kst(snapshot.as_of_cutoff)
    except ValueError:
        return False


def _brain_snapshot_errors(
    root: Path,
    *,
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
    payload_path: ArtifactReference,
) -> list[str]:
    snapshot = arm.as_of_snapshot
    if snapshot is None:
        return []
    label = f"{case.case_id}:{arm.arm_id}"
    path = _safe_artifact_path(root, payload_path.artifact_path)
    if path is None:
        return [f"shadow_brain_snapshot_path_invalid:{label}"]
    expected_path = (
        root
        / "brain"
        / "snapshots"
        / snapshot.brain_version
        / "brain_manifest.json"
    ).resolve()
    if path != expected_path:
        return [f"shadow_brain_snapshot_path_mismatch:{label}"]
    try:
        manifest = BrainManifest.model_validate(read_json(path))
    except (OSError, ValueError):
        return [f"shadow_brain_snapshot_contract_invalid:{label}"]
    production_snapshot = next(
        (
            item.as_of_snapshot
            for item in case.arms
            if item.arm_id == "C" and item.as_of_snapshot is not None
        ),
        None,
    )
    if production_snapshot is None:
        return [f"shadow_brain_production_snapshot_missing:{label}"]
    if (
        manifest.brain_version != snapshot.brain_version
        or manifest.build_mode != "llm-full"
        or manifest.production_eligible is not True
        or manifest.coverage_complete is not True
        or manifest.production_memory_snapshot_id != production_snapshot.snapshot_id
        or manifest.production_memory_corpus_sha256
        != snapshot.corpus_manifest_sha256
        or manifest.production_memory_source_generation_sha256
        != snapshot.source_generation_sha256
        or manifest.brain_record_cutoff_at is None
        or manifest.production_memory_as_of_cutoff is None
        or as_kst(manifest.brain_record_cutoff_at)
        != as_kst(snapshot.as_of_cutoff)
        or as_kst(manifest.production_memory_as_of_cutoff)
        != as_kst(snapshot.as_of_cutoff)
    ):
        return [f"shadow_brain_snapshot_projection_mismatch:{label}"]
    return []


def _production_retrieved_record_errors(
    root: Path,
    *,
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
    manifest: MemoryCellSnapshotManifest,
) -> list[str]:
    label = f"{case.case_id}:{arm.arm_id}"
    expected_ids = [record.record_id for record in arm.retrieved_records]
    if not expected_ids:
        return []
    database_path = _safe_artifact_path(root, manifest.database.artifact_path)
    if database_path is None:
        return [f"shadow_snapshot_database_path_invalid:{label}"]
    connection = duckdb.connect(str(database_path), read_only=True)
    try:
        rows = connection.execute(
            """
            SELECT record_id, independent_unit_id, record_type, memory_lanes,
                   evidence_polarity, trade_date, available_from, regime_cluster
            FROM records
            WHERE record_id IN (SELECT UNNEST(?::VARCHAR[]))
            ORDER BY record_id
            """,
            [expected_ids],
        ).fetchall()
    except duckdb.Error:
        return [f"shadow_snapshot_record_projection_query_failed:{label}"]
    finally:
        connection.close()
    observed: dict[str, tuple[object, ...]] = {str(row[0]): row for row in rows}
    if set(observed) != set(expected_ids):
        return [f"shadow_snapshot_retrieved_record_ids_mismatch:{label}"]
    for record in arm.retrieved_records:
        row = observed[record.record_id]
        try:
            lanes = json.loads(str(row[3]))
            available_from = parse_datetime(str(row[6]))
        except (json.JSONDecodeError, ValueError):
            return [f"shadow_snapshot_record_projection_invalid:{label}"]
        trade_date = row[5]
        if isinstance(trade_date, datetime):
            trade_date = trade_date.date()
        if (
            str(row[1]) != record.independent_unit_id
            or _independent_unit_ticker(str(row[1])) != record.ticker
            or str(row[2]) != record.record_type
            or lanes != record.memory_lanes
            or str(row[4]) != record.evidence_polarity
            or trade_date != record.trade_date
            or as_kst(available_from) != as_kst(record.available_from)
            or str(row[7]) != record.regime_cluster
        ):
            return [f"shadow_snapshot_record_projection_mismatch:{label}"]
    return []


def _independent_unit_ticker(independent_unit_id: str) -> str | None:
    prefix, separator, _ = independent_unit_id.partition(":")
    if not separator or prefix not in {
        "EVENT_ISSUER_DAY",
        "ISSUER_DAY",
        "THEME_DAY_TICKER_DAY",
        "TICKER_DAY",
    }:
        return None
    value = independent_unit_id.rsplit(":", 1)[-1].strip()
    return value or None


def _legacy_snapshot_errors(
    root: Path,
    *,
    case: ShadowReplayCase,
    arm: ShadowArmObservation,
    payload: dict[str, Any],
) -> list[str]:
    label = f"{case.case_id}:{arm.arm_id}"
    errors: list[str] = []
    top_ids = payload.get("top_record_ids")
    if (
        not isinstance(top_ids, list)
        or any(not isinstance(value, str) or not value for value in top_ids)
        or len(top_ids) > 3
        or len(top_ids) != len(set(top_ids))
        or top_ids != [record.record_id for record in arm.retrieved_records]
    ):
        errors.append(f"shadow_legacy_top3_record_ids_mismatch:{label}")
        return errors
    if payload.get("record_count") != len(top_ids):
        errors.append(f"shadow_legacy_top3_record_count_mismatch:{label}")
    if not _record_store_generation_allows(
        root,
        expected_generation=str(payload["source_generation_sha256"]),
        cutoff=case.replay_cutoff_at,
    ):
        errors.append(f"shadow_legacy_top3_source_generation_stale:{label}")
    try:
        index_ref = ArtifactReference.model_validate(payload.get("index_artifact"))
    except ValueError:
        errors.append(f"shadow_legacy_top3_index_reference_invalid:{label}")
        return errors
    index_path = _safe_artifact_path(root, index_ref.artifact_path)
    if index_path is None or not index_path.exists():
        errors.append(f"shadow_legacy_top3_index_missing:{label}")
        return errors
    if file_sha256(index_path) != index_ref.sha256:
        errors.append(f"shadow_legacy_top3_index_hash_mismatch:{label}")
    try:
        rows = [
            json.loads(line)
            for line in index_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, json.JSONDecodeError):
        errors.append(f"shadow_legacy_top3_index_invalid:{label}")
        return errors
    if len(rows) != index_ref.item_count or [
        row.get("record_id") if isinstance(row, dict) else None for row in rows
    ] != top_ids:
        errors.append(f"shadow_legacy_top3_index_projection_mismatch:{label}")
    production_snapshot = next(
        (
            item.as_of_snapshot
            for item in case.arms
            if item.arm_id == "C" and item.as_of_snapshot is not None
        ),
        None,
    )
    if production_snapshot is None:
        errors.append(f"shadow_legacy_production_snapshot_missing:{label}")
        return errors
    production_path = _safe_artifact_path(
        root,
        production_snapshot.snapshot_manifest.artifact_path,
    )
    if production_path is None:
        errors.append(f"shadow_legacy_production_snapshot_path_invalid:{label}")
        return errors
    try:
        production_manifest = MemoryCellSnapshotManifest.model_validate(
            read_json(production_path)
        )
    except (OSError, ValueError):
        errors.append(f"shadow_legacy_production_snapshot_contract_invalid:{label}")
        return errors
    errors.extend(
        _production_retrieved_record_errors(
            root,
            case=case,
            arm=arm,
            manifest=production_manifest,
        )
    )
    return errors


def _record_store_generation_allows(
    root: Path,
    *,
    expected_generation: str,
    cutoff: datetime,
) -> bool:
    path = root / "memory" / "record_index" / "manifest.json"
    try:
        payload = read_json(path)
    except (OSError, ValueError):
        return False
    if not isinstance(payload, dict) or payload.get("schema_version") != (
        "nslab.record_index_manifest.v2"
    ) or payload.get("record_hash_kind") != "canonical_full_envelope_sha256":
        return False
    if payload.get("generation_root_sha256") == expected_generation:
        return True
    history = payload.get("generation_history")
    if not isinstance(history, dict):
        return False
    changed_min = history.get(expected_generation)
    if not isinstance(changed_min, str):
        return False
    try:
        return as_kst(parse_datetime(changed_min)) > as_kst(cutoff)
    except ValueError:
        return False


def _safe_artifact_path(root: Path, value: str) -> Path | None:
    if not value or value != value.strip() or "\\" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    path = (root / Path(*pure.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return None
    return path


def _sealed_split_path(root: Path, split: ShadowDatasetSplit) -> Path:
    split_id = (
        "SPLIT-"
        + split.pre_registration_attestation.commitment_sha256[:20].upper()
    )
    return (root / SHADOW_SPLIT_ROOT / split_id / SHADOW_SPLIT_FILE).resolve()


def _sealed_dataset_path(root: Path, dataset: ShadowReplayDataset) -> Path:
    dataset_id = "DATASET-" + dataset.dataset_attestation.commitment_sha256[:20].upper()
    return (root / SHADOW_DATASET_ROOT / dataset_id / SHADOW_DATASET_FILE).resolve()


def _artifact_reference(
    root: Path,
    path: Path,
    content: bytes,
    *,
    item_count: int,
) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative_to_root(path, root),
        sha256=sha256_text(content.decode("utf-8")),
        item_count=item_count,
    )


def _json_bytes(payload: Any) -> bytes:
    return (canonical_json(payload) + "\n").encode("utf-8")


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return "".join(canonical_json(row) + "\n" for row in rows).encode("utf-8")


def _jsonl_count(content: bytes) -> int:
    return sum(1 for line in content.decode("utf-8").splitlines() if line.strip())


def _write_immutable_bytes(path: Path, content: bytes) -> None:
    if path.exists():
        if path.read_bytes() != content:
            raise ValueError(f"immutable shadow artifact conflict: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_immutable_json(path: Path, payload: dict[str, Any]) -> None:
    expected = _json_bytes(payload)
    if path.exists():
        if path.read_bytes() != expected:
            raise ValueError(f"immutable shadow manifest conflict: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, payload)
    if path.read_bytes() != expected:
        path.write_bytes(expected)
