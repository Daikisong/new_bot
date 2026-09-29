"""Sealed A/B/C evaluation on the deployable one-call daily path."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from news_scalping_lab.brain.category_index import CategoryBrainIndex
from news_scalping_lab.brain.offline_v2 import (
    BrainPackageDailyContextProvider,
    _daily_query_texts,
)
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import BlindPrediction
from news_scalping_lab.contracts.offline_brain import (
    BrainInformedDecision,
    CurrentDayInterpretation,
    CurrentEventCapsule,
    DailyBrainContext,
    SynthesizedMechanismClaim,
    ThinDailyRunManifest,
)
from news_scalping_lab.contracts.quality_evaluation import (
    BlindRuntimeCase,
    BlindRuntimeSelection,
    QualityArtifactReference,
    QualityEvaluationProfile,
    SealedBlindCaseInputManifest,
    SharedDMinusOneContext,
    ThinDailyQualityPredictionManifest,
    ThinDailyQualitySeal,
    quality_full_runtime_profile,
)
from news_scalping_lab.evaluation.quality_runtime import (
    load_blind_runtime_selection,
    load_runtime_outcome_selection,
    materialize_blind_case_news,
)
from news_scalping_lab.evaluation.replay_snapshot import _record_ids_from_hash_ledger
from news_scalping_lab.evaluation.runtime_variant_shadow import (
    QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    _prediction_metrics,
)
from news_scalping_lab.evaluation.semantic_upgrade_split import (
    SEMANTIC_UPGRADE_SPLIT_ROOT,
    SEMANTIC_UPGRADE_SPLIT_VERSION,
)
from news_scalping_lab.inference.thin_daily import (
    THIN_DAILY_ARCHITECTURE_VERSION,
    ThinDailyAnalyzer,
    _validate_brain_context_as_of,
)
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.policies import EmbeddingFallbackPolicy, EvidencePolicy
from news_scalping_lab.retrieval.production_embedding import (
    create_configured_embedding_provider,
)
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    now_kst,
    relative_to_root,
    sha256_bytes,
    sha256_text,
    stable_id,
    write_json,
)

THIN_DAILY_QUALITY_VERSION = "nslab.thin_daily_quality.v2"
BUILD_ONLY_SOURCE_ATTESTATION_VERSION = "nslab.thin_daily_build_source_attestation.v1"
THIN_DAILY_QUALITY_ARMS: tuple[Literal["A", "B", "C"], ...] = ("A", "B", "C")
THIN_DAILY_QUALITY_ROOT = Path("runs/semantic_brain_upgrade/thin_daily_quality")
BASELINE_CLAIM_QUERY_CANDIDATES = 512
BASELINE_DAILY_CLAIM_LIMIT = 24


@dataclass(frozen=True)
class ThinDailyQualityPredictionResult:
    manifest: ThinDailyQualityPredictionManifest
    manifest_path: Path


@dataclass(frozen=True)
class ThinDailyQualityScoreResult:
    report: dict[str, Any]
    report_path: Path
    markdown_path: Path


@dataclass(frozen=True)
class BuildOnlyV2SourceAttestation:
    payload: dict[str, Any]
    split_case_ids: dict[str, list[str]]
    build_cutoff: datetime
    attestation_sha256: str


class NoHistoricalBrainContextProvider:
    """The A arm uses only current news and a stable empty-brain marker."""

    async def retrieve(
        self,
        *,
        interpretation: CurrentDayInterpretation | None,
        current_event_capsules: list[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        if interpretation is not None:
            raise ValueError("thin daily evaluation must retrieve from current news only")
        if max_exact_witnesses < 0:
            raise ValueError("daily witness limit cannot be negative")
        return DailyBrainContext(
            brain_version="NO_HISTORICAL_BRAIN",
            brain_package_root=sha256_text("nslab.thin_daily_quality.arm_a.empty_brain.v1"),
            brain_build_cutoff=cutoff_at,
            brain_projection_mode="NO_HISTORICAL_BRAIN",
            retrieval_basis="CURRENT_NEWS",
            current_event_capsules_sha256=_current_capsule_sha256(current_event_capsules),
            retrieval_query_count=0,
            index_query_count=0,
        )


class BaselineBrainDailyContextProvider:
    """Bounded point-in-time ANN reader for the immutable legacy category brain."""

    def __init__(
        self,
        *,
        repository_root: Path,
        baseline_project_root: Path,
        baseline_manifest_path: Path,
        category_index_manifest_path: Path,
        embedding_provider: Any,
    ) -> None:
        self.repository_root = repository_root.resolve()
        self.baseline_project_root = baseline_project_root.resolve()
        self.baseline_manifest_path = baseline_manifest_path.resolve()
        self.category_index_manifest_path = category_index_manifest_path.resolve()
        self.embedding_provider = embedding_provider
        self._index: CategoryBrainIndex | None = None
        self._baseline_manifest_sha256 = ""
        self._brain_version = ""
        self._brain_root = ""

    def ensure_ready(self) -> None:
        if self._index is not None:
            return
        baseline = _read_json_object(self.baseline_manifest_path)
        if (
            baseline.get("schema_version") != "nslab.semantic_brain_upgrade_baseline.v1"
            or baseline.get("status") != "IMMUTABLE_STAGING_BASELINE"
            or baseline.get("production_activation_status") != "NOT_PRODUCTION_ACTIVATED"
        ):
            raise ValueError("baseline brain manifest is not the expected immutable evaluation asset")
        declared_project = baseline.get("project_root")
        if not isinstance(declared_project, str):
            raise ValueError("baseline brain manifest omitted its project root")
        resolved_declared_project = (self.repository_root / declared_project).resolve()
        if resolved_declared_project != self.baseline_project_root:
            raise ValueError("baseline project root differs from its frozen manifest")
        if not _is_relative_to(self.baseline_manifest_path, self.repository_root):
            raise ValueError("baseline manifest escapes the evaluation repository")
        if not _is_relative_to(self.category_index_manifest_path, self.baseline_project_root):
            raise ValueError("baseline category index escapes the immutable staging project")
        brain = baseline.get("brain")
        if not isinstance(brain, dict):
            raise ValueError("baseline brain identity is missing")
        brain_version = brain.get("version")
        brain_root = brain.get("root_sha256")
        if not isinstance(brain_version, str) or not isinstance(brain_root, str):
            raise ValueError("baseline brain identity is invalid")
        index = CategoryBrainIndex(
            self.baseline_project_root,
            self.category_index_manifest_path,
            embedding_provider=self.embedding_provider,
        )
        if index.manifest.brain_version != brain_version:
            index.close()
            raise ValueError("baseline category index points at a different brain version")
        if not index.manifest.hnsw_index_ready:
            index.close()
            raise ValueError("baseline category brain HNSW index is not ready")
        self._baseline_manifest_sha256 = file_sha256(self.baseline_manifest_path)
        self._brain_version = brain_version
        self._brain_root = brain_root
        self._index = index

    def close(self) -> None:
        if self._index is not None:
            self._index.close()
            self._index = None

    async def retrieve(
        self,
        *,
        interpretation: CurrentDayInterpretation | None,
        current_event_capsules: list[CurrentEventCapsule],
        cutoff_at: datetime,
        max_exact_witnesses: int,
    ) -> DailyBrainContext:
        if interpretation is not None:
            raise ValueError("baseline evaluation retrieval cannot depend on model interpretation")
        if max_exact_witnesses < 0:
            raise ValueError("daily witness limit cannot be negative")
        self.ensure_ready()
        assert self._index is not None
        query_texts = _daily_query_texts(None, current_event_capsules)
        vectors = await self.embedding_provider.embed(
            texts=query_texts,
            purpose="thin_daily_baseline_brain_retrieval",
        )
        if len(vectors) != len(query_texts):
            raise ValueError("baseline brain embedding count mismatch")
        scores: dict[str, tuple[float, Any]] = {}
        for vector in vectors:
            for score, claim in self._index.query_candidates(
                query_vector=vector,
                limit=BASELINE_CLAIM_QUERY_CANDIDATES,
                available_before=cutoff_at,
            ):
                previous = scores.get(claim.claim_id)
                if previous is None or score > previous[0]:
                    scores[claim.claim_id] = (score, claim)
        selected = sorted(
            scores.values(),
            key=lambda row: (-row[0], row[1].claim_id),
        )[:BASELINE_DAILY_CLAIM_LIMIT]
        selected_ids = {claim.claim_id for _score, claim in selected}
        proofs = self._index.claim_proofs_by_ids(selected_ids)
        index_manifest_sha256 = file_sha256(self.category_index_manifest_path)
        claims = [
            SynthesizedMechanismClaim(
                claim_id=claim.claim_id,
                category=claim.category,
                statement=claim.statement,
                mechanism=claim.mechanism,
                conditions=claim.conditions,
                boundary_conditions=claim.boundary_conditions,
                failure_modes=claim.failure_modes,
                source_node_ids=[
                    f"baseline-brain:{self._brain_root}",
                    f"baseline-manifest:{self._baseline_manifest_sha256}",
                    f"category-index-manifest:{index_manifest_sha256}",
                    f"claim-inclusion:{claim.claim_id}:{proofs[claim.claim_id].leaf_index}",
                ],
                available_from=claim.available_from,
                confidence=claim.confidence_label,
                status=claim.status,
            )
            for _score, claim in selected
        ]
        return DailyBrainContext(
            brain_version=self._brain_version,
            brain_package_root=self._brain_root,
            brain_build_cutoff=self._index.manifest.brain_record_cutoff_at,
            brain_projection_mode="POINT_IN_TIME_EVIDENCE_ONLY",
            retrieval_basis="CURRENT_NEWS",
            current_event_capsules_sha256=_current_capsule_sha256(current_event_capsules),
            selected_mechanism_claims=claims,
            retrieval_query_count=len(query_texts),
            index_query_count=len(query_texts),
        )


async def predict_thin_daily_quality(
    repository_root: Path,
    *,
    settings: Settings,
    blind_selection_path: Path,
    baseline_project_root: Path,
    baseline_manifest_path: Path,
    category_index_manifest_path: Path,
    offline_package_dir: Path,
    profile: QualityEvaluationProfile,
) -> ThinDailyQualityPredictionResult:
    """Predict all A/B/C cases without accepting or resolving any outcome path."""

    root = repository_root.resolve()
    if settings.project_root.resolve() != root:
        raise ValueError("thin daily evaluation settings must use the repository root")
    if profile.profile != "QUALITY_FULL":
        raise ValueError("thin daily A/B/C evaluation requires QUALITY_FULL")
    if settings.llm.max_retries > 1:
        raise ValueError("thin daily evaluation permits at most one structured repair")
    selection_path = blind_selection_path.resolve()
    selection = load_blind_runtime_selection(root, selection_path)
    if selection.selection_policy != "ALL_SOURCE_SPLIT_CASES":
        raise ValueError("formal thin daily evaluation requires a full split, not a smoke sample")
    split_names = {case.split for case in selection.cases}
    if len(split_names) != 1:
        raise ValueError("thin daily evaluation selection must contain exactly one split")
    blind_reference = _artifact_reference(root, selection_path)

    settings.evidence_policy = EvidencePolicy.CSV_MEMORY_ONLY_STRICT
    settings.event_cluster_fallback_policy = EmbeddingFallbackPolicy.FAIL_CLOSED
    base_llm = create_llm_provider(settings)
    actual_profile = quality_full_runtime_profile(
        provider=str(getattr(base_llm, "provider_name", settings.llm_provider)),
        model=str(getattr(base_llm, "model", settings.llm.model)),
        reasoning_effort=str(
            getattr(base_llm, "reasoning_effort", settings.llm.reasoning_effort)
        ),
    )
    if actual_profile != profile:
        raise ValueError("thin daily runtime model identity differs from the sealed profile")
    embedding_provider = create_configured_embedding_provider(
        settings,
        production=True,
        llm_provider=base_llm,
    )
    if getattr(embedding_provider, "production_capability_attested", False) is not True:
        raise ValueError("thin daily evaluation requires the verified production embedding provider")

    baseline_provider = BaselineBrainDailyContextProvider(
        repository_root=root,
        baseline_project_root=baseline_project_root,
        baseline_manifest_path=baseline_manifest_path,
        category_index_manifest_path=category_index_manifest_path,
        embedding_provider=embedding_provider,
    )
    package_provider = BrainPackageDailyContextProvider(
        settings,
        package_dir=offline_package_dir.resolve(),
        embedding_provider=embedding_provider,
        allow_point_in_time_projection=True,
    )
    baseline_provider.ensure_ready()
    package_provider.ensure_ready()
    assert package_provider.package_dir is not None
    assert package_provider._manifest is not None
    build_source = _validate_build_only_v2_package(package_provider.package_dir)
    _validate_quality_cases_against_build_split(
        selection.cases,
        split_case_ids=build_source.split_case_ids,
        build_cutoff=build_source.build_cutoff,
    )
    baseline_architecture_sha256 = sha256_text(
        canonical_json(
            {
                "version": THIN_DAILY_QUALITY_VERSION,
                "arm": "B",
                "brain_root": baseline_provider._brain_root,
                "baseline_manifest_sha256": baseline_provider._baseline_manifest_sha256,
                "category_index_manifest_sha256": file_sha256(category_index_manifest_path),
                "daily_architecture": THIN_DAILY_ARCHITECTURE_VERSION,
            }
        )
    )
    package_manifest_path = package_provider.package_dir / "brain_package_manifest.json"
    package_manifest_sha256 = file_sha256(package_manifest_path)
    package_architecture_sha256 = _thin_daily_c_architecture_sha256(
        brain_root=package_provider._manifest.package_root,
        package_manifest_sha256=package_manifest_sha256,
        build_source_attestation_sha256=build_source.attestation_sha256,
    )
    arm_architectures = {
        "A": sha256_text(
            canonical_json(
                {
                    "version": THIN_DAILY_QUALITY_VERSION,
                    "arm": "A",
                    "historical_brain": False,
                    "daily_architecture": THIN_DAILY_ARCHITECTURE_VERSION,
                }
            )
        ),
        "B": baseline_architecture_sha256,
        "C": package_architecture_sha256,
    }
    run_id = stable_id(
        "THINQUAL",
        THIN_DAILY_QUALITY_VERSION,
        selection.selection_id,
        blind_reference.sha256,
        profile.model_dump(mode="json"),
        arm_architectures,
        length=20,
    )
    output_dir = root / THIN_DAILY_QUALITY_ROOT / next(iter(split_names)).lower() / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    attestation_path = output_dir / "build_only_source_attestation.json"
    if attestation_path.exists():
        if _read_json_object(attestation_path) != build_source.payload:
            raise ValueError("existing BUILD-only source attestation drifted")
    else:
        write_json(attestation_path, build_source.payload)
    build_source_reference = _artifact_reference(root, attestation_path)
    manifest_path = output_dir / "paired_thin_daily_predictions.json"
    manifest = _load_or_create_prediction_manifest(
        manifest_path,
        run_id=run_id,
        profile=profile,
        blind_reference=blind_reference,
        build_source_reference=build_source_reference,
        selection=selection,
        arm_architectures=arm_architectures,
    )
    seal_map = {(seal.case_id, seal.arm_id): seal for seal in manifest.seals}
    providers: dict[str, Any] = {
        "A": NoHistoricalBrainContextProvider(),
        "B": baseline_provider,
        "C": package_provider,
    }
    analyzers = {
        arm_id: ThinDailyAnalyzer(
            settings,
            llm=base_llm,
            embedding_provider=embedding_provider,
            brain_context_provider=providers[arm_id],
            run_output_root=output_dir / "daily_runs" / arm_id,
            write_canonical_outputs=False,
        )
        for arm_id in THIN_DAILY_QUALITY_ARMS
    }
    try:
        for case in selection.cases:
            case_output = output_dir / "cases" / case.episode_id
            blind_input = materialize_blind_case_news(
                root,
                case=case,
                output_dir=case_output,
            )
            for arm_id in THIN_DAILY_QUALITY_ARMS:
                if (case.episode_id, arm_id) in seal_map:
                    _verify_existing_prediction_seal(
                        root,
                        selection_case=case,
                        seal=seal_map[(case.episode_id, arm_id)],
                        expected_architecture_sha256=arm_architectures[arm_id],
                        profile=profile,
                    )
                    continue
                analysis = await analyzers[arm_id].analyze(
                    news_csv=blind_input.news_csv_path,
                    trade_date=case.trade_date,
                    cutoff_at=case.cutoff_at,
                    d_minus_one_context_path=blind_input.d_minus_one_context_path,
                )
                run_manifest_path = (
                    output_dir
                    / "daily_runs"
                    / arm_id
                    / analysis.run_id
                    / "thin_daily_run_manifest.json"
                )
                run_manifest_ref = _artifact_reference(root, run_manifest_path)
                current_run_manifest = ThinDailyRunManifest.model_validate(
                    _read_json_reference(root, run_manifest_ref)
                )
                context_reference = _manifest_artifact_reference(
                    root,
                    current_run_manifest.daily_brain_context_artifact,
                    current_run_manifest.daily_brain_context_sha256,
                )
                daily_context = DailyBrainContext.model_validate(
                    _read_json_reference(root, context_reference)
                )
                _verify_runtime_manifest(
                    current_run_manifest,
                    case=case,
                    prediction=analysis.blind_prediction,
                    profile=profile,
                    expected_brain_root=analysis.context_manifest.brain_package_root,
                )
                seal = ThinDailyQualitySeal(
                    case_id=case.episode_id,
                    arm_id=arm_id,
                    arm_architecture_sha256=arm_architectures[arm_id],
                    sealed_at=now_kst(),
                    cutoff_at=case.cutoff_at,
                    blind_input_manifest=case.blind_input_manifest,
                    materialized_news_csv=_artifact_reference(root, blind_input.news_csv_path),
                    news_sha256=blind_input.news_sha256,
                    d_minus_one_context=blind_input.d_minus_one_context_reference,
                    d_minus_one_payload_sha256=blind_input.d_minus_one_payload_sha256,
                    current_event_capsules=_manifest_artifact_reference(
                        root,
                        current_run_manifest.current_event_capsules_artifact,
                        current_run_manifest.current_event_capsules_sha256,
                    ),
                    daily_brain_context=_manifest_artifact_reference(
                        root,
                        current_run_manifest.daily_brain_context_artifact,
                        current_run_manifest.daily_brain_context_sha256,
                    ),
                    prediction=_manifest_artifact_reference(
                        root,
                        current_run_manifest.prediction_artifact,
                        current_run_manifest.prediction_sha256,
                    ),
                    run_manifest=run_manifest_ref,
                    maximum_live_agent_call_count=current_run_manifest.maximum_live_agent_call_count,
                    material_event_cluster_count=current_run_manifest.material_event_cluster_count,
                    current_event_capsule_count=current_run_manifest.current_event_capsule_count,
                    selected_semantic_capsule_count=len(
                        daily_context.selected_semantic_capsules
                    ),
                    selected_mechanism_claim_count=len(
                        daily_context.selected_mechanism_claims
                    ),
                    compiled_brain_guidance_count=current_run_manifest.compiled_brain_guidance_count,
                    historical_raw_witness_count=current_run_manifest.historical_raw_witness_count,
                    wall_clock_seconds=current_run_manifest.wall_clock_seconds,
                    token_counts=current_run_manifest.token_counts,
                    llm_model_config=current_run_manifest.llm_model_config,
                    brain_version=current_run_manifest.brain_version,
                    brain_package_root=current_run_manifest.brain_package_root,
                    brain_projection_mode=current_run_manifest.brain_projection_mode,
                )
                manifest = _manifest_with_seal(manifest, seal)
                seal_map[(case.episode_id, arm_id)] = seal
                _write_prediction_manifest(manifest_path, manifest)
    finally:
        baseline_provider.close()
    if not manifest.all_predictions_sealed:
        raise RuntimeError("thin daily A/B/C evaluation ended before every prediction was sealed")
    return ThinDailyQualityPredictionResult(manifest=manifest, manifest_path=manifest_path)


def score_thin_daily_quality(
    repository_root: Path,
    *,
    paired_prediction_manifest_path: Path,
    outcome_selection_path: Path,
) -> ThinDailyQualityScoreResult:
    """Score only after validating complete A/B/C prediction and citation closure."""

    root = repository_root.resolve()
    paired_path = paired_prediction_manifest_path.resolve()
    if not _is_relative_to(paired_path, root):
        raise ValueError("thin daily paired prediction manifest escapes the repository root")
    paired = ThinDailyQualityPredictionManifest.model_validate(_read_json_object(paired_path))
    if not paired.all_predictions_sealed or paired.outcome_opened:
        raise ValueError("thin daily scoring requires complete predictions sealed before outcomes")
    if paired.expected_arm_ids != ["A", "B", "C"]:
        raise ValueError("thin daily scoring requires the complete A/B/C arm set")
    if profile_identity(paired.profile) != ("codex-oauth", "gpt-5.6-sol", "xhigh"):
        raise ValueError("thin daily quality manifest has an unsupported model identity")

    blind_path = _resolve_artifact_path(root, paired.blind_selection)
    selection = load_blind_runtime_selection(root, blind_path)
    if _artifact_reference(root, blind_path) != paired.blind_selection:
        raise ValueError("thin daily paired manifest blind selection hash drifted")
    if [case.episode_id for case in selection.cases] != paired.expected_case_ids:
        raise ValueError("thin daily paired manifest case population differs from BLIND selection")
    build_source_attestation = _read_json_reference(
        root,
        paired.build_only_source_attestation,
    )
    _verify_build_only_attestation_payload(build_source_attestation)
    _validate_quality_cases_against_build_split(
        selection.cases,
        split_case_ids=build_source_attestation["split_case_ids"],
        build_cutoff=datetime.fromisoformat(build_source_attestation["build_cutoff"]),
    )
    expected_c_architecture = _thin_daily_c_architecture_sha256(
        brain_root=build_source_attestation["package_root"],
        package_manifest_sha256=build_source_attestation["package_manifest_sha256"],
        build_source_attestation_sha256=build_source_attestation["attestation_sha256"],
    )
    if paired.expected_arm_architecture_sha256["C"] != expected_c_architecture:
        raise ValueError("thin daily C arm is not bound to its BUILD-only source attestation")
    case_by_id = {case.episode_id: case for case in selection.cases}
    seal_by_key = {(seal.case_id, seal.arm_id): seal for seal in paired.seals}

    # Validate every prediction-side artifact and citation closure before opening outcomes.
    for case in selection.cases:
        for arm_id in THIN_DAILY_QUALITY_ARMS:
            seal = seal_by_key[(case.episode_id, arm_id)]
            _verify_sealed_prediction_artifacts(
                root,
                selection_case=case,
                seal=seal,
                expected_architecture_sha256=paired.expected_arm_architecture_sha256[arm_id],
                profile=paired.profile,
            )

    outcome_path = outcome_selection_path.resolve()
    if not _is_relative_to(outcome_path, root):
        raise ValueError("thin daily outcome selection escapes the repository root")
    outcome_selection = load_runtime_outcome_selection(outcome_path)
    if (
        outcome_selection.selection_id != selection.selection_id
        or outcome_selection.blind_selection_sha256 != paired.blind_selection.sha256
        or {case.episode_id for case in outcome_selection.cases} != set(paired.expected_case_ids)
    ):
        raise ValueError("thin daily outcome selection differs from the sealed blind population")
    outcome_by_case = {case.episode_id: case for case in outcome_selection.cases}
    outcome_selection_ref = _artifact_reference(root, outcome_path)

    per_case: list[dict[str, Any]] = []
    for case_id in paired.expected_case_ids:
        case = case_by_id[case_id]
        seal_a = seal_by_key[(case_id, "A")]
        d1 = _read_d_minus_one_for_seal(root, case, seal_a)
        outcome_case = outcome_by_case[case_id]
        outcome_bytes = _read_verified_bytes(root, outcome_case.outcome_ledger)
        arm_rows: dict[str, dict[str, Any]] = {}
        for arm_id in THIN_DAILY_QUALITY_ARMS:
            seal = seal_by_key[(case_id, arm_id)]
            prediction, context, _capsules = _load_sealed_prediction_artifacts(root, seal)
            metrics = _prediction_metrics(
                prediction,
                None,
                truth_bytes=outcome_bytes,
                evaluation_universe_tickers=d1.candidate_universe,
                probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
                allow_unsupported_tickers=True,
            )
            arm_rows[arm_id] = {
                "metrics": metrics,
                "citation_closure_passed": _citation_closure_passed(
                    prediction,
                    context,
                    _load_event_capsules(
                        root,
                        seal,
                        expected_run_id=prediction.context_manifest_id,
                    ),
                ),
                "brain_evidence_item_count": (
                    seal.selected_semantic_capsule_count
                    + seal.selected_mechanism_claim_count
                    + seal.compiled_brain_guidance_count
                ),
                "logical_llm_call_count": seal.logical_llm_call_count,
                "maximum_live_agent_call_count": seal.maximum_live_agent_call_count,
                "wall_clock_seconds": seal.wall_clock_seconds,
                "prompt_tokens_estimate": sum(seal.token_counts.values()),
                "witness_count": seal.historical_raw_witness_count,
            }
        per_case.append(
            {
                "case_id": case_id,
                "trade_date": case.trade_date.isoformat(),
                "split": case.split,
                "arms": arm_rows,
            }
        )

    aggregates: dict[str, dict[str, Any]] = {}
    for arm_id in THIN_DAILY_QUALITY_ARMS:
        aggregates[arm_id] = _aggregate_arm_metrics(per_case, arm_id)
    report = {
        "schema_version": "nslab.thin_daily_quality_score_report.v2",
        "run_id": paired.run_id,
        "prediction_manifest_sha256": file_sha256(paired_path),
        "blind_selection_id": selection.selection_id,
        "blind_selection_sha256": paired.blind_selection.sha256,
        "build_only_source_attestation": paired.build_only_source_attestation.model_dump(
            mode="json"
        ),
        "build_only_source_summary": {
            "attestation_sha256": build_source_attestation["attestation_sha256"],
            "source_memory_snapshot_id": build_source_attestation[
                "source_memory_snapshot_id"
            ],
            "build_cutoff": build_source_attestation["build_cutoff"],
            "source_record_count": build_source_attestation["source_record_count"],
            "build_snapshot_record_count": build_source_attestation[
                "build_snapshot_record_count"
            ],
            "calibration_case_count": build_source_attestation[
                "calibration_case_count"
            ],
            "calibration_record_count": build_source_attestation[
                "calibration_record_count"
            ],
            "calibration_overlap_count": build_source_attestation[
                "calibration_overlap_count"
            ],
            "holdout_case_count": build_source_attestation["holdout_case_count"],
            "holdout_record_count": build_source_attestation["holdout_record_count"],
            "holdout_overlap_count": build_source_attestation["holdout_overlap_count"],
            "full_corpus_centroids_used": build_source_attestation[
                "full_corpus_centroids_used"
            ],
            "generated_embedding_count": build_source_attestation[
                "generated_embedding_count"
            ],
        },
        "outcome_selection_id": outcome_selection.selection_id,
        "outcome_selection_sha256": outcome_selection_ref.sha256,
        "split": selection.cases[0].split,
        "case_count": len(selection.cases),
        "arm_count": len(THIN_DAILY_QUALITY_ARMS),
        "paired_case_count": len(paired.paired_case_ids),
        "profile": paired.profile.model_dump(mode="json"),
        "outcome_opened_after_prediction_closure": True,
        "production_activation_status": "NOT_PRODUCTION_ACTIVATED",
        "latency_is_blocking": False,
        "architecture_gates": _architecture_gate_report(paired),
        "quality_metrics_by_arm": aggregates,
        "paired_deltas": _paired_metric_deltas(aggregates),
        "per_case": per_case,
        "unavailable_metrics": {
            "sector_recall_precision": "UNAVAILABLE_NO_SEALED_SECTOR_TRUTH_LABELS",
            "theme_breadth_accuracy": "UNAVAILABLE_NO_SEALED_THEME_BREADTH_TRUTH_LABELS",
            "newsless_hallucination": "UNAVAILABLE_NO_SEALED_NEWSLESS_TRUTH_LABELS",
        },
        "promotion_decision": "HOLD_FOR_REGISTERED_QUALITY_GATES_AND_EXTERNAL_REVIEW",
    }
    output_dir = paired_path.parent / "scoring"
    report_path = output_dir / "thin_daily_quality_score_report.json"
    markdown_path = output_dir / "thin_daily_quality_score_report.md"
    write_json(report_path, report)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    markdown_path.write_text(_render_score_markdown(report), encoding="utf-8", newline="\n")
    return ThinDailyQualityScoreResult(report, report_path, markdown_path)


def _validate_build_only_v2_package(package_dir: Path) -> BuildOnlyV2SourceAttestation:
    package = package_dir.resolve()
    compile_path = package / "offline_compile_manifest.json"
    package_manifest_path = package / "brain_package_manifest.json"
    if not compile_path.is_file() or not package_manifest_path.is_file():
        raise FileNotFoundError("V2 package omitted its compile or package manifest")
    compile_manifest = _read_json_object(compile_path)
    package_manifest = _read_json_object(package_manifest_path)
    if not bool(compile_manifest.get("source_pointer_manifest_hash_match")):
        raise ValueError("V2 source memory pointer hash was not verified at build time")
    source_project_value = compile_manifest.get("source_project")
    if not isinstance(source_project_value, str) or not source_project_value.strip():
        raise ValueError("V2 compile manifest omitted its source project")
    source_project = Path(source_project_value).resolve()
    if not source_project.is_dir():
        raise FileNotFoundError("V2 BUILD source project is missing")

    pointer_path = source_project / "memory" / "retrieval_index" / "current.json"
    pointer = _read_json_object(pointer_path)
    if pointer.get("evaluation_only") is not True:
        raise ValueError("formal V2 evaluation requires an evaluation-only BUILD snapshot")
    snapshot_id = pointer.get("snapshot_id")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("BUILD snapshot pointer omitted its identity")
    if compile_manifest.get("source_memory_snapshot_id") != snapshot_id:
        raise ValueError("V2 package was not compiled from the selected BUILD snapshot")

    pointer_manifest_sha256 = pointer.get("manifest_sha256")
    if not isinstance(pointer_manifest_sha256, str):
        raise ValueError("BUILD snapshot pointer omitted its manifest hash")
    if (
        compile_manifest.get("source_memory_manifest_sha256") != pointer_manifest_sha256
        or compile_manifest.get("source_pointer_manifest_sha256") != pointer_manifest_sha256
    ):
        raise ValueError("V2 compile manifest differs from the BUILD snapshot manifest")
    if package_manifest.get("memory_snapshot_root") != pointer_manifest_sha256:
        raise ValueError("V2 package memory root differs from the BUILD snapshot")

    snapshot_manifest_path = _source_project_artifact_path(
        source_project,
        pointer.get("manifest_path"),
        expected_sha256=pointer_manifest_sha256,
        label="BUILD snapshot manifest",
    )
    snapshot_manifest = _read_json_object(snapshot_manifest_path)
    if (
        snapshot_manifest.get("snapshot_id") != snapshot_id
        or snapshot_manifest.get("evaluation_only") is not True
        or snapshot_manifest.get("availability_mode") != "replay_available_from"
    ):
        raise ValueError("selected memory snapshot is not the sealed replay BUILD snapshot")
    build_cutoff_value = snapshot_manifest.get("as_of_cutoff")
    if not isinstance(build_cutoff_value, str):
        raise ValueError("BUILD snapshot omitted its cutoff")
    build_cutoff = datetime.fromisoformat(build_cutoff_value)
    if build_cutoff.utcoffset() is None:
        raise ValueError("BUILD snapshot cutoff must be timezone-aware")

    snapshot_record_count = snapshot_manifest.get("record_count")
    if not isinstance(snapshot_record_count, int) or snapshot_record_count < 1:
        raise ValueError("BUILD snapshot record count is invalid")
    if (
        compile_manifest.get("record_count") != snapshot_record_count
        or package_manifest.get("record_count") != snapshot_record_count
        or package_manifest.get("build_cutoff") != build_cutoff_value
    ):
        raise ValueError("V2 package population differs from its BUILD snapshot")

    database_path = _source_project_artifact_path(
        source_project,
        snapshot_manifest.get("database"),
        label="BUILD snapshot database",
    )
    database_ref = snapshot_manifest["database"]
    if pointer.get("evaluation_database_sha256") != database_ref.get("sha256"):
        raise ValueError("BUILD snapshot database commitment differs from its pointer")
    if not database_path.is_file():
        raise FileNotFoundError("BUILD snapshot database is missing")

    record_index_path = source_project / "memory" / "record_index" / "manifest.json"
    record_index = _read_json_object(record_index_path)
    record_corpus_root = record_index.get("full_envelope_root_sha256")
    source_record_count = record_index.get("record_count")
    if (
        not isinstance(record_corpus_root, str)
        or not isinstance(source_record_count, int)
        or compile_manifest.get("record_corpus_root") != record_corpus_root
        or package_manifest.get("record_corpus_root") != record_corpus_root
    ):
        raise ValueError("V2 package record corpus root differs from its source project")
    embedding_identity = snapshot_manifest.get("embedding_model")
    if (
        snapshot_manifest.get("real_embedding") is not True
        or compile_manifest.get("embedding_identity") != embedding_identity
    ):
        raise ValueError("BUILD snapshot does not attest to the reused real embeddings")

    replay_receipt_sha256 = pointer.get("evaluation_receipt_sha256")
    if not isinstance(replay_receipt_sha256, str):
        raise ValueError("BUILD snapshot pointer omitted its replay receipt hash")
    replay_receipt_path = _source_project_artifact_path(
        source_project,
        pointer.get("evaluation_receipt_path"),
        expected_sha256=replay_receipt_sha256,
        label="BUILD replay receipt",
    )
    replay_receipt = _read_json_object(replay_receipt_path)
    if (
        replay_receipt.get("schema_version") != "nslab.shadow_replay_as_of_snapshot.v1"
        or replay_receipt.get("availability_mode") != "replay_available_from"
        or replay_receipt.get("source_snapshot_record_count") != source_record_count
        or replay_receipt.get("snapshot_id") != snapshot_id
        or replay_receipt.get("build_cutoff") != build_cutoff_value
        or replay_receipt.get("record_count") != snapshot_record_count
        or replay_receipt.get("calibration_overlap_count") != 0
        or replay_receipt.get("holdout_overlap_count") != 0
        or replay_receipt.get("full_corpus_centroids_used") is not False
        or replay_receipt.get("generated_embedding_count") != 0
    ):
        raise ValueError("BUILD replay receipt does not prove split exclusion and reuse")

    source_hashes_path = _source_project_artifact_path(
        source_project,
        snapshot_manifest.get("source_record_hashes"),
        label="BUILD record hash ledger",
    )
    source_hashes_sha256 = file_sha256(source_hashes_path)
    if replay_receipt.get("source_record_hashes_sha256") != source_hashes_sha256:
        raise ValueError("BUILD record hash ledger differs from its replay receipt")
    included_record_ids = _record_ids_from_hash_ledger(source_hashes_path)
    if len(included_record_ids) != snapshot_record_count:
        raise ValueError("BUILD record hash ledger count differs from its snapshot")

    split_selection_path = source_project / SEMANTIC_UPGRADE_SPLIT_ROOT / "shadow_case_selection.json"
    split_selection = _read_json_object(split_selection_path)
    if split_selection.get("schema_version") != SEMANTIC_UPGRADE_SPLIT_VERSION:
        raise ValueError("BUILD source split selection has an unsupported schema")
    split_plan_path = source_project / SEMANTIC_UPGRADE_SPLIT_ROOT / "shadow_split_plan.json"
    if split_selection.get("plan_sha256") != file_sha256(split_plan_path):
        raise ValueError("BUILD source split plan hash differs from its selection")
    evaluation_brain = _read_json_object(source_project / "brain" / "current" / "brain_manifest.json")
    evaluation_brain_version = evaluation_brain.get("brain_version")
    if not isinstance(evaluation_brain_version, str) or not evaluation_brain_version:
        raise ValueError("BUILD source evaluation brain omitted its version")
    evaluation_brain_receipt_path = (
        source_project
        / "runs"
        / "semantic_brain_upgrade"
        / "evaluation_brains"
        / evaluation_brain_version
        / "evaluation_brain_receipt.json"
    )
    evaluation_brain_receipt = _read_json_object(evaluation_brain_receipt_path)
    split_selection_sha256 = file_sha256(split_selection_path)
    if (
        evaluation_brain_receipt.get("evaluation_only") is not True
        or evaluation_brain_receipt.get("memory_snapshot_id") != snapshot_id
        or evaluation_brain_receipt.get("split_selection_sha256") != split_selection_sha256
    ):
        raise ValueError("BUILD replay snapshot and evaluation split receipt are not linked")

    split_case_ids: dict[str, list[str]] = {"BUILD": [], "CALIBRATION": [], "HOLDOUT": []}
    seen_episode_ids: set[str] = set()
    raw_cases = split_selection.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("BUILD source split selection has no cases")
    for row in raw_cases:
        if not isinstance(row, dict):
            raise ValueError("BUILD source split selection contains an invalid case")
        episode_id = row.get("episode_id")
        split = row.get("split")
        if (
            not isinstance(episode_id, str)
            or not episode_id
            or split not in split_case_ids
            or episode_id in seen_episode_ids
        ):
            raise ValueError("BUILD source split selection has invalid or duplicate cases")
        split_case_ids[split].append(episode_id)
        seen_episode_ids.add(episode_id)
    expected_case_counts = {
        "BUILD": split_selection.get("build_case_count"),
        "CALIBRATION": split_selection.get("calibration_case_count"),
        "HOLDOUT": split_selection.get("holdout_case_count"),
    }
    if any(len(split_case_ids[name]) != expected_case_counts[name] for name in split_case_ids):
        raise ValueError("BUILD source split case counts do not close")
    if any(not split_case_ids[name] for name in split_case_ids):
        raise ValueError("BUILD source split is missing a partition")

    calibration_record_ids = _record_ids_for_episodes(
        source_project,
        split_case_ids["CALIBRATION"],
    )
    holdout_record_ids = _record_ids_for_episodes(source_project, split_case_ids["HOLDOUT"])
    if (
        len(calibration_record_ids) != replay_receipt.get("calibration_record_count")
        or len(holdout_record_ids) != replay_receipt.get("holdout_record_count")
        or sha256_text("\n".join(sorted(calibration_record_ids)))
        != replay_receipt.get("calibration_record_ids_sha256")
        or sha256_text("\n".join(sorted(holdout_record_ids)))
        != replay_receipt.get("holdout_record_ids_sha256")
    ):
        raise ValueError("BUILD replay receipt record exclusions differ from the sealed split")
    calibration_overlap = calibration_record_ids & included_record_ids
    holdout_overlap = holdout_record_ids & included_record_ids
    if calibration_overlap or holdout_overlap:
        raise ValueError("CALIBRATION or HOLDOUT records entered the V2 BUILD source")

    if package_manifest.get("production_eligible") is not False:
        raise ValueError("evaluation-only V2 package cannot be marked production eligible")
    package_manifest_sha256 = file_sha256(package_manifest_path)
    package_root = package_manifest.get("package_root")
    if not isinstance(package_root, str) or not package_root:
        raise ValueError("V2 package manifest omitted its package root")
    core = {
        "schema_version": BUILD_ONLY_SOURCE_ATTESTATION_VERSION,
        "source_project": source_project.as_posix(),
        "source_pointer_sha256": file_sha256(pointer_path),
        "source_memory_snapshot_id": snapshot_id,
        "source_memory_manifest_sha256": pointer_manifest_sha256,
        "source_record_hashes_sha256": source_hashes_sha256,
        "source_record_count": replay_receipt.get("source_snapshot_record_count"),
        "build_snapshot_record_count": snapshot_record_count,
        "build_cutoff": build_cutoff_value,
        "calibration_case_count": len(split_case_ids["CALIBRATION"]),
        "calibration_record_count": len(calibration_record_ids),
        "calibration_record_ids_sha256": replay_receipt.get("calibration_record_ids_sha256"),
        "calibration_overlap_count": 0,
        "holdout_case_count": len(split_case_ids["HOLDOUT"]),
        "holdout_record_count": len(holdout_record_ids),
        "holdout_record_ids_sha256": replay_receipt.get("holdout_record_ids_sha256"),
        "holdout_overlap_count": 0,
        "full_corpus_centroids_used": False,
        "generated_embedding_count": 0,
        "embedding_identity": embedding_identity,
        "split_selection_sha256": split_selection_sha256,
        "split_plan_sha256": file_sha256(split_plan_path),
        "split_case_ids": {name: sorted(ids) for name, ids in split_case_ids.items()},
        "replay_receipt_path": replay_receipt_path.relative_to(source_project).as_posix(),
        "replay_receipt_sha256": str(replay_receipt_sha256),
        "evaluation_brain_receipt_path": evaluation_brain_receipt_path.relative_to(
            source_project
        ).as_posix(),
        "evaluation_brain_receipt_sha256": file_sha256(evaluation_brain_receipt_path),
        "offline_compile_manifest_sha256": file_sha256(compile_path),
        "compile_id": compile_manifest.get("compile_id"),
        "package_manifest_sha256": package_manifest_sha256,
        "package_root": package_root,
        "brain_version": package_manifest.get("brain_version"),
    }
    attestation_sha256 = sha256_text(canonical_json(core))
    payload = {**core, "attestation_sha256": attestation_sha256}
    return BuildOnlyV2SourceAttestation(
        payload=payload,
        split_case_ids={name: sorted(ids) for name, ids in split_case_ids.items()},
        build_cutoff=build_cutoff,
        attestation_sha256=attestation_sha256,
    )


def _validate_quality_cases_against_build_split(
    cases: list[BlindRuntimeCase],
    *,
    split_case_ids: dict[str, list[str]],
    build_cutoff: datetime,
) -> None:
    build_ids = set(split_case_ids.get("BUILD", []))
    calibration_ids = set(split_case_ids.get("CALIBRATION", []))
    holdout_ids = set(split_case_ids.get("HOLDOUT", []))
    if build_ids & calibration_ids or build_ids & holdout_ids or calibration_ids & holdout_ids:
        raise ValueError("BUILD source split case partitions overlap")
    for case in cases:
        if case.split == "CALIBRATION" and case.episode_id in calibration_ids:
            continue
        if case.split == "HOLDOUT" and case.episode_id in holdout_ids:
            continue
        if case.split == "POST_CUTOFF":
            if case.episode_id in build_ids | calibration_ids | holdout_ids:
                raise ValueError("post-cutoff case overlaps the historical evaluation split")
            if case.trade_date <= build_cutoff.date():
                raise ValueError("post-cutoff case is not after the BUILD snapshot cutoff")
            continue
        raise ValueError("evaluation case does not belong to its attested BUILD split")


def _verify_build_only_attestation_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("BUILD-only source attestation is not an object")
    if payload.get("schema_version") != BUILD_ONLY_SOURCE_ATTESTATION_VERSION:
        raise ValueError("BUILD-only source attestation schema is unsupported")
    attestation_sha256 = payload.get("attestation_sha256")
    core = {key: value for key, value in payload.items() if key != "attestation_sha256"}
    if not isinstance(attestation_sha256, str) or sha256_text(canonical_json(core)) != attestation_sha256:
        raise ValueError("BUILD-only source attestation hash is invalid")
    if (
        payload.get("calibration_overlap_count") != 0
        or payload.get("holdout_overlap_count") != 0
        or payload.get("full_corpus_centroids_used") is not False
        or payload.get("generated_embedding_count") != 0
    ):
        raise ValueError("BUILD-only source attestation does not close its exclusion gates")


def _thin_daily_c_architecture_sha256(
    *,
    brain_root: str,
    package_manifest_sha256: str,
    build_source_attestation_sha256: str,
) -> str:
    return sha256_text(
        canonical_json(
            {
                "version": THIN_DAILY_QUALITY_VERSION,
                "arm": "C",
                "brain_root": brain_root,
                "package_manifest_sha256": package_manifest_sha256,
                "daily_architecture": THIN_DAILY_ARCHITECTURE_VERSION,
                "point_in_time_projection": True,
                "build_source_attestation_sha256": build_source_attestation_sha256,
            }
        )
    )


def _record_ids_for_episodes(project_root: Path, episode_ids: list[str]) -> set[str]:
    record_ids: set[str] = set()
    records_dir = project_root / "memory" / "records"
    for episode_id in episode_ids:
        path = records_dir / f"{episode_id}.jsonl"
        if not path.is_file():
            raise FileNotFoundError(f"split record shard is missing: {episode_id}")
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                record_id = row.get("record_id") if isinstance(row, dict) else None
                if not isinstance(record_id, str) or not record_id:
                    raise ValueError("split record shard contains an invalid record ID")
                record_ids.add(record_id)
    return record_ids


def _source_project_artifact_path(
    project_root: Path,
    reference: Any,
    *,
    expected_sha256: str | None = None,
    label: str,
) -> Path:
    if isinstance(reference, dict):
        artifact_path = reference.get("artifact_path")
        expected_sha256 = reference.get("sha256", expected_sha256)
    else:
        artifact_path = reference
    if not isinstance(artifact_path, str) or not artifact_path:
        raise ValueError(f"{label} path is missing")
    path = (project_root / artifact_path).resolve()
    if not _is_relative_to(path, project_root):
        raise ValueError(f"{label} escapes the source project")
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing")
    if expected_sha256 is not None and file_sha256(path) != expected_sha256:
        raise ValueError(f"{label} hash differs from its commitment")
    return path


def _load_or_create_prediction_manifest(
    path: Path,
    *,
    run_id: str,
    profile: QualityEvaluationProfile,
    blind_reference: QualityArtifactReference,
    build_source_reference: QualityArtifactReference,
    selection: BlindRuntimeSelection,
    arm_architectures: dict[str, str],
) -> ThinDailyQualityPredictionManifest:
    if path.is_file():
        manifest = ThinDailyQualityPredictionManifest.model_validate(_read_json_object(path))
        if (
            manifest.run_id != run_id
            or manifest.profile != profile
            or manifest.blind_selection != blind_reference
            or manifest.build_only_source_attestation != build_source_reference
            or manifest.expected_case_ids != [case.episode_id for case in selection.cases]
            or manifest.expected_arm_architecture_sha256 != arm_architectures
        ):
            raise ValueError("thin daily prediction manifest identity changed; refusing resume")
        return manifest
    return ThinDailyQualityPredictionManifest(
        run_id=run_id,
        profile=profile,
        blind_selection=blind_reference,
        build_only_source_attestation=build_source_reference,
        expected_case_ids=[case.episode_id for case in selection.cases],
        expected_arm_ids=list(THIN_DAILY_QUALITY_ARMS),
        expected_arm_architecture_sha256=arm_architectures,
    )


def _manifest_with_seal(
    manifest: ThinDailyQualityPredictionManifest,
    seal: ThinDailyQualitySeal,
) -> ThinDailyQualityPredictionManifest:
    seals = [row for row in manifest.seals if (row.case_id, row.arm_id) != (seal.case_id, seal.arm_id)]
    seals.append(seal)
    seals.sort(key=lambda row: (manifest.expected_case_ids.index(row.case_id), row.arm_id))
    arms = set(manifest.expected_arm_ids)
    paired = sorted(
        case_id
        for case_id in manifest.expected_case_ids
        if {row.arm_id for row in seals if row.case_id == case_id} == arms
    )
    updated = manifest.model_copy(
        update={
            "seals": seals,
            "paired_case_ids": paired,
            "all_predictions_sealed": len(seals) == len(manifest.expected_case_ids) * len(arms),
        }
    )
    return ThinDailyQualityPredictionManifest.model_validate(
        updated.model_dump(mode="python")
    )


def _write_prediction_manifest(
    path: Path,
    manifest: ThinDailyQualityPredictionManifest,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(canonical_json(manifest.model_dump(mode="json")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _verify_existing_prediction_seal(
    root: Path,
    *,
    selection_case: BlindRuntimeCase,
    seal: ThinDailyQualitySeal,
    expected_architecture_sha256: str,
    profile: QualityEvaluationProfile,
) -> None:
    if seal.arm_architecture_sha256 != expected_architecture_sha256:
        raise ValueError("existing thin daily prediction seal has a different arm identity")
    if seal.blind_input_manifest != selection_case.blind_input_manifest:
        raise ValueError("existing thin daily prediction seal has a different blind input")
    _verify_sealed_prediction_artifacts(
        root,
        selection_case=selection_case,
        seal=seal,
        expected_architecture_sha256=expected_architecture_sha256,
        profile=profile,
    )


def _verify_sealed_prediction_artifacts(
    root: Path,
    *,
    selection_case: BlindRuntimeCase,
    seal: ThinDailyQualitySeal,
    expected_architecture_sha256: str,
    profile: QualityEvaluationProfile,
) -> None:
    if (
        seal.case_id != selection_case.episode_id
        or seal.arm_architecture_sha256 != expected_architecture_sha256
        or seal.news_sha256 != selection_case.news_sha256
        or seal.d_minus_one_payload_sha256 != selection_case.d_minus_one_payload_sha256
        or as_kst(seal.cutoff_at) != as_kst(selection_case.cutoff_at)
        or seal.blind_input_manifest != selection_case.blind_input_manifest
    ):
        raise ValueError("thin daily prediction seal differs from its blind case")
    input_manifest = SealedBlindCaseInputManifest.model_validate(
        _read_json_reference(root, seal.blind_input_manifest)
    )
    if (
        input_manifest.episode_id != selection_case.episode_id
        or input_manifest.news_csv.sha256 != seal.news_sha256
        or input_manifest.d_minus_one_context != seal.d_minus_one_context
        or input_manifest.d_minus_one_payload_sha256 != seal.d_minus_one_payload_sha256
    ):
        raise ValueError("thin daily seal is not bound to its sealed CSV/D-1 input")
    materialized_news_path = _resolve_artifact_path(
        root,
        seal.materialized_news_csv,
    )
    if file_sha256(materialized_news_path) != seal.materialized_news_csv.sha256:
        raise ValueError("thin daily materialized CSV changed after sealing")
    if seal.materialized_news_csv.sha256 != seal.news_sha256:
        raise ValueError("thin daily materialized CSV hash differs from BLIND selection")
    run_manifest = ThinDailyRunManifest.model_validate(
        _read_json_reference(root, seal.run_manifest)
    )
    prediction, context, capsules = _load_sealed_prediction_artifacts(root, seal)
    if profile_identity_from_config(run_manifest.llm_model_config) != profile_identity(profile):
        raise ValueError("thin daily run manifest model differs from the sealed quality profile")
    _verify_runtime_manifest(
        run_manifest,
        case=selection_case,
        prediction=prediction,
        profile=profile,
        expected_brain_root=context.brain_package_root,
    )
    if (
        run_manifest.run_id != prediction.context_manifest_id
        or run_manifest.brain_version != seal.brain_version
        or run_manifest.brain_package_root != seal.brain_package_root
        or run_manifest.brain_projection_mode != seal.brain_projection_mode
        or len(context.selected_semantic_capsules) != seal.selected_semantic_capsule_count
        or len(context.selected_mechanism_claims) != seal.selected_mechanism_claim_count
        or len(context.compiled_brain_guidance) != seal.compiled_brain_guidance_count
        or len(context.exact_witnesses) != seal.historical_raw_witness_count
    ):
        raise ValueError("thin daily seal differs from its persisted brain context")
    if len(capsules) != seal.current_event_capsule_count:
        raise ValueError("thin daily current event capsule count differs from its seal")
    for artifact_path, artifact_sha256 in (
        (run_manifest.brain_decision_artifact, run_manifest.brain_decision_sha256),
        (run_manifest.row_disposition_artifact, run_manifest.row_disposition_sha256),
        (run_manifest.report_artifact, run_manifest.report_sha256),
    ):
        _manifest_artifact_reference(root, artifact_path, artifact_sha256)
    decision_reference = _manifest_artifact_reference(
        root,
        run_manifest.brain_decision_artifact,
        run_manifest.brain_decision_sha256,
    )
    decision = BrainInformedDecision.model_validate(
        _read_json_reference(root, decision_reference)
    )
    expected_cluster_ids = {capsule.cluster_id for capsule in capsules}
    if (
        len(decision.analyzed_cluster_ids) != len(expected_cluster_ids)
        or set(decision.analyzed_cluster_ids) != expected_cluster_ids
    ):
        raise ValueError("thin daily sealed decision did not account for every material cluster")
    row_reference = _manifest_artifact_reference(
        root,
        run_manifest.row_disposition_artifact,
        run_manifest.row_disposition_sha256,
    )
    row_payload = _read_json_reference(root, row_reference)
    row_numbers = [row.get("row_number") for row in row_payload.get("rows", [])]
    if (
        row_payload.get("schema_version") != "nslab.thin_daily_row_dispositions.v1"
        or row_payload.get("run_id") != run_manifest.run_id
        or row_payload.get("row_count") != run_manifest.total_news_row_count
        or len(row_numbers) != run_manifest.total_news_row_count
        or sorted(row_numbers) != list(range(1, run_manifest.total_news_row_count + 1))
    ):
        raise ValueError("thin daily row disposition ledger is not complete")
    if not _citation_closure_passed(prediction, context, capsules):
        raise ValueError("thin daily prediction citation closure is invalid")


def _verify_runtime_manifest(
    manifest: ThinDailyRunManifest,
    *,
    case: BlindRuntimeCase,
    prediction: BlindPrediction,
    profile: QualityEvaluationProfile,
    expected_brain_root: str,
) -> None:
    if (
        manifest.trade_date != case.trade_date
        or as_kst(manifest.cutoff_at) != as_kst(case.cutoff_at)
        or manifest.news_sha256 != case.news_sha256
        or manifest.cutoff_safe_news_row_count != case.cutoff_safe_news_row_count
        or manifest.total_news_row_count != case.cutoff_safe_news_row_count
        or manifest.row_disposition_count != manifest.total_news_row_count
        or manifest.brain_package_root != expected_brain_root
        or manifest.evidence_policy != "CSV_MEMORY_ONLY_STRICT"
        or manifest.logical_llm_call_count != 1
        or manifest.maximum_live_agent_call_count not in (1, 2)
        or manifest.llm_purposes != ["final_market_decision"]
        or not manifest.brain_context_loaded_before_first_llm
        or manifest.brain_retrieval_basis != "CURRENT_NEWS"
        or any(
            (
                manifest.historical_raw_daily_map_call_count,
                manifest.daily_import_call_count,
                manifest.daily_brain_rebuild_call_count,
                manifest.blind_web_search_call_count,
                manifest.online_full_corpus_scan_count,
                manifest.future_record_count,
            )
        )
        or profile_identity_from_config(manifest.llm_model_config) != profile_identity(profile)
        or prediction.trade_date != case.trade_date
        or as_kst(prediction.cutoff_at) != as_kst(case.cutoff_at)
        or prediction.context_manifest_id != manifest.run_id
    ):
        raise ValueError("thin daily run manifest violated the sealed evaluation contract")
    if not _verify_prediction_digest(prediction):
        raise ValueError("thin daily prediction digest is invalid")


def _load_sealed_prediction_artifacts(
    root: Path,
    seal: ThinDailyQualitySeal,
) -> tuple[BlindPrediction, DailyBrainContext, list[CurrentEventCapsule]]:
    prediction = BlindPrediction.model_validate(_read_json_reference(root, seal.prediction))
    context = DailyBrainContext.model_validate(_read_json_reference(root, seal.daily_brain_context))
    capsules = _load_event_capsules(
        root,
        seal,
        expected_run_id=prediction.context_manifest_id,
    )
    if _current_capsule_sha256(capsules) != context.current_event_capsules_sha256:
        raise ValueError("thin daily event capsule artifact is not bound to the brain context")
    _validate_brain_context_as_of(context, cutoff_at=seal.cutoff_at)
    if not _verify_prediction_digest(prediction):
        raise ValueError("thin daily prediction digest is invalid")
    return prediction, context, capsules


def _load_event_capsules(
    root: Path,
    seal: ThinDailyQualitySeal,
    *,
    expected_run_id: str | None = None,
) -> list[CurrentEventCapsule]:
    payload = _read_json_reference(root, seal.current_event_capsules)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("thin daily current event capsule artifact is malformed")
    if payload.get("schema_version") != "nslab.current_event_capsule_set.v1":
        raise ValueError("thin daily current event capsule artifact schema is invalid")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("thin daily event capsule artifact is not bound to a run")
    if expected_run_id is not None and run_id != expected_run_id:
        raise ValueError("thin daily event capsule artifact run ID differs from its prediction")
    return [CurrentEventCapsule.model_validate(row) for row in payload["items"]]


def _citation_closure_passed(
    prediction: BlindPrediction,
    context: DailyBrainContext,
    capsules: list[CurrentEventCapsule],
) -> bool:
    allowed_events = {event_id for row in capsules for event_id in row.event_ids}
    allowed_rows = {row_id for row in capsules for row_id in row.source_row_ids}
    allowed_capsules = {row.capsule_id for row in context.selected_semantic_capsules}
    allowed_claims = {row.claim_id for row in context.selected_mechanism_claims}
    allowed_records = {row.record_id for row in context.exact_witnesses}
    for capsule in context.selected_semantic_capsules:
        allowed_records.update(
            {
                *capsule.supporting_record_ids,
                *capsule.contradicting_record_ids,
                *capsule.near_miss_record_ids,
                *capsule.counterexample_record_ids,
                *capsule.newsless_or_unexplained_record_ids,
                *capsule.error_record_ids,
            }
        )
    allowed_population_roots = {
        str(row["population_root"])
        for row in context.population_statistics
        if isinstance(row.get("population_root"), str)
    }
    cited_brain_ids: set[str] = set()
    for candidate in prediction.candidates:
        if (
            not candidate.source_row_ids
            or not set(candidate.event_ids).issubset(allowed_events)
            or not set(candidate.source_row_ids).issubset(allowed_rows)
            or not set(candidate.semantic_capsule_ids).issubset(allowed_capsules)
            or not set(candidate.mechanism_claim_ids).issubset(allowed_claims)
            or not set(candidate.population_manifest_roots).issubset(allowed_population_roots)
            or not {
                *candidate.memory_record_ids,
                *candidate.prior_positive_record_ids,
                *candidate.prior_negative_record_ids,
            }.issubset(allowed_records)
        ):
            return False
        cited_brain_ids.update(candidate.semantic_capsule_ids)
        cited_brain_ids.update(candidate.mechanism_claim_ids)
    for sector in prediction.dominant_sectors:
        if (
            not set(sector.triggering_events).issubset(allowed_events)
            or not set(sector.semantic_capsule_ids).issubset(allowed_capsules)
            or not set(sector.mechanism_claim_ids).issubset(allowed_claims)
            or not set(sector.population_manifest_roots).issubset(allowed_population_roots)
            or not {
                *sector.supporting_record_ids,
                *sector.contradicting_record_ids,
            }.issubset(allowed_records)
        ):
            return False
        cited_brain_ids.update(sector.semantic_capsule_ids)
        cited_brain_ids.update(sector.mechanism_claim_ids)
    return not (allowed_capsules or allowed_claims) or bool(cited_brain_ids)


def _read_d_minus_one_for_seal(
    root: Path,
    case: BlindRuntimeCase,
    seal: ThinDailyQualitySeal,
) -> SharedDMinusOneContext:
    payload = _read_json_reference(root, seal.d_minus_one_context)
    context = SharedDMinusOneContext.model_validate(payload)
    if (
        context.trade_date != case.trade_date
        or as_kst(context.cutoff_at) != as_kst(case.cutoff_at)
        or context.allowed_through != case.trade_date - timedelta(days=1)
        or sha256_text(canonical_json(context.model_dump(mode="json")))
        != seal.d_minus_one_payload_sha256
    ):
        raise ValueError("thin daily scoring D-1 context is not cutoff-safe or hash-bound")
    return context


def _aggregate_arm_metrics(per_case: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    arm_rows = [row["arms"][arm_id] for row in per_case]
    ticker_metrics = [row["metrics"] for row in arm_rows]
    averages: dict[str, Any] = {}
    metric_keys = [
        "leader_selection_accuracy",
        "population_brier",
        "population_expected_calibration_error",
    ]
    for cutoff in (5, 10, 20):
        for target in ("upper_limit", "high20", "high10"):
            metric_keys.append(f"{target}_recall_at_{cutoff}")
            metric_keys.append(f"{target}_precision_at_{cutoff}")
    for key in metric_keys:
        values = [float(row[key]) for row in ticker_metrics if row.get(key) is not None]
        averages[key] = math.fsum(values) / len(values) if values else None
    predicted_count = sum(len(row.get("generated_candidate_tickers", [])) for row in ticker_metrics)
    unsupported_count = sum(int(row.get("unsupported_d1_ticker_count", 0)) for row in ticker_metrics)
    averages.update(
        {
            "case_count": len(arm_rows),
            "unsupported_ticker_count": unsupported_count,
            "generated_candidate_count": predicted_count,
            "unsupported_ticker_rate": unsupported_count / predicted_count if predicted_count else None,
            "citation_closure_pass_count": sum(bool(row["citation_closure_passed"]) for row in arm_rows),
            "brain_nonempty_case_count": sum(int(row["brain_evidence_item_count"] > 0) for row in arm_rows),
            "logical_llm_call_count": sum(int(row["logical_llm_call_count"]) for row in arm_rows),
            "maximum_live_agent_call_count": sum(int(row["maximum_live_agent_call_count"]) for row in arm_rows),
            "wall_clock_seconds_mean": math.fsum(float(row["wall_clock_seconds"]) for row in arm_rows) / len(arm_rows),
            "prompt_tokens_estimate": sum(int(row["prompt_tokens_estimate"]) for row in arm_rows),
            "historical_raw_witness_count": sum(int(row["witness_count"]) for row in arm_rows),
        }
    )
    return averages


def _paired_metric_deltas(aggregates: dict[str, dict[str, Any]]) -> dict[str, Any]:
    delta_keys = (
        "leader_selection_accuracy",
        "population_brier",
        "upper_limit_recall_at_5",
        "upper_limit_recall_at_10",
        "upper_limit_recall_at_20",
        "high20_recall_at_5",
        "high20_recall_at_10",
        "high20_recall_at_20",
        "high10_recall_at_5",
        "high10_recall_at_10",
        "high10_recall_at_20",
    )
    return {
        f"C_minus_{arm}": {
            key: (
                aggregates["C"][key] - aggregates[arm][key]
                if aggregates["C"].get(key) is not None and aggregates[arm].get(key) is not None
                else None
            )
            for key in delta_keys
        }
        for arm in ("A", "B")
    }


def _architecture_gate_report(manifest: ThinDailyQualityPredictionManifest) -> dict[str, Any]:
    seals = manifest.seals
    return {
        "all_cases_have_A_B_C_seals": manifest.all_predictions_sealed,
        "normal_daily_llm_calls_exactly_one": all(seal.logical_llm_call_count == 1 for seal in seals),
        "maximum_live_calls_at_most_two": all(seal.maximum_live_agent_call_count <= 2 for seal in seals),
        "historical_raw_daily_map_calls_zero": all(seal.historical_raw_daily_map_call_count == 0 for seal in seals),
        "daily_import_and_rebuild_calls_zero": all(
            seal.daily_import_call_count == 0 and seal.daily_brain_rebuild_call_count == 0 for seal in seals
        ),
        "blind_web_and_full_scans_zero": all(
            seal.blind_web_search_call_count == 0
            and seal.online_full_corpus_scan_count == 0
            and seal.future_record_count == 0
            for seal in seals
        ),
        "exact_witnesses_bounded_to_24": all(seal.historical_raw_witness_count <= 24 for seal in seals),
        "outcomes_not_opened_during_prediction": manifest.outcome_opened is False,
        "production_activation_status": manifest.production_activation_status,
    }


def _render_score_markdown(report: dict[str, Any]) -> str:
    source = report["build_only_source_summary"]
    source_ref = report["build_only_source_attestation"]
    lines = [
        "# Thin Daily A/B/C Quality Evaluation",
        "",
        f"- Split: {report['split']}",
        f"- Cases: {report['case_count']}",
        f"- Prediction run: `{report['run_id']}`",
        "- Architecture: one shared `ThinDailyAnalyzer` path; one normal LLM call, at most two with repair.",
        "- Outcome opened only after complete prediction and citation closure.",
        (
            f"- BUILD snapshot: `{source['source_memory_snapshot_id']}` at "
            f"`{source['build_cutoff']}`; "
            f"{source['build_snapshot_record_count']:,}/"
            f"{source['source_record_count']:,} source records included."
        ),
        (
            f"- Exclusion audit: CAL {source['calibration_record_count']:,} records / "
            f"{source['calibration_overlap_count']} overlap; HOLDOUT "
            f"{source['holdout_record_count']:,} records / "
            f"{source['holdout_overlap_count']} overlap; full-corpus centroids "
            f"`{str(source['full_corpus_centroids_used']).lower()}`."
        ),
        (
            f"- BUILD attestation: `{source_ref['artifact_path']}` "
            f"(SHA-256 `{source['attestation_sha256']}`)."
        ),
        "- Production activation: `NOT_PRODUCTION_ACTIVATED`.",
        "",
        (
            "| Arm | Leader | UL R@5 | UL R@10 | UL R@20 | H20 R@5 | H20 R@10 | "
            "H20 R@20 | Brier | Unsupported | Brain cases |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for arm_id in THIN_DAILY_QUALITY_ARMS:
        row = report["quality_metrics_by_arm"][arm_id]
        values = [
            row.get("leader_selection_accuracy"),
            row.get("upper_limit_recall_at_5"),
            row.get("upper_limit_recall_at_10"),
            row.get("upper_limit_recall_at_20"),
            row.get("high20_recall_at_5"),
            row.get("high20_recall_at_10"),
            row.get("high20_recall_at_20"),
            row.get("population_brier"),
            row.get("unsupported_ticker_count"),
            row.get("brain_nonempty_case_count"),
        ]
        formatted = [
            "NA"
            if value is None
            else f"{value:.4f}"
            if isinstance(value, float)
            else str(value)
            for value in values
        ]
        lines.append("| " + arm_id + " | " + " | ".join(formatted) + " |")
    lines.extend(
        [
            "",
            "## Unavailable Truth Labels",
            "",
            (
                "Sector recall/precision, theme breadth, and newsless-hallucination "
                "truth labels are absent from the sealed outcomes. They are not inferred."
            ),
            "",
            f"Promotion decision: `{report['promotion_decision']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def _current_capsule_sha256(capsules: list[CurrentEventCapsule]) -> str:
    return sha256_text(canonical_json([row.model_dump(mode="json") for row in capsules]))


def _verify_prediction_digest(prediction: BlindPrediction) -> bool:
    expected = prediction.blind_artifact_sha256
    without_digest = prediction.model_copy(update={"blind_artifact_sha256": None})
    return expected == sha256_text(canonical_json(without_digest.model_dump(mode="json")))


def _profile_identity(profile: QualityEvaluationProfile) -> tuple[str, str, str]:
    return profile.provider, profile.model, profile.reasoning_effort


def profile_identity(profile: QualityEvaluationProfile) -> tuple[str, str, str]:
    return _profile_identity(profile)


def profile_identity_from_config(config: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(config.get("provider", "")),
        str(config.get("model", "")),
        str(config.get("reasoning_effort", "")),
    )


def _artifact_reference(root: Path, path: Path) -> QualityArtifactReference:
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if not _is_relative_to(resolved_path, resolved_root):
        raise ValueError("thin daily evaluation artifact escaped the repository root")
    return QualityArtifactReference(
        artifact_path=relative_to_root(resolved_path, resolved_root),
        sha256=file_sha256(resolved_path),
    )


def _manifest_artifact_reference(
    root: Path,
    artifact_path: str,
    sha256: str,
) -> QualityArtifactReference:
    reference = QualityArtifactReference(artifact_path=artifact_path, sha256=sha256)
    path = _resolve_artifact_path(root, reference)
    if file_sha256(path) != sha256:
        raise ValueError("thin daily runtime artifact hash mismatch")
    return reference


def _resolve_artifact_path(root: Path, reference: QualityArtifactReference) -> Path:
    resolved_root = root.resolve()
    resolved_path = (resolved_root / reference.artifact_path).resolve()
    if not _is_relative_to(resolved_path, resolved_root):
        raise ValueError("quality artifact reference escapes repository root")
    return resolved_path


def _read_verified_bytes(root: Path, reference: QualityArtifactReference) -> bytes:
    path = _resolve_artifact_path(root, reference)
    payload = path.read_bytes()
    if sha256_bytes(payload) != reference.sha256:
        raise ValueError(f"quality artifact hash mismatch: {reference.artifact_path}")
    return payload


def _read_json_reference(root: Path, reference: QualityArtifactReference) -> Any:
    payload = _read_verified_bytes(root, reference)
    try:
        return json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"quality JSON artifact is malformed: {reference.artifact_path}") from exc


def _read_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True
