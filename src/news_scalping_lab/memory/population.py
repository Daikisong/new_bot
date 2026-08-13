"""Deterministic observed-population retrieval over selected memory cells."""

from __future__ import annotations

import hashlib
import json
import math
import os
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, cast

from news_scalping_lab.contracts.memory_context import (
    POPULATION_PURPOSE_LANES,
    POPULATION_PURPOSE_UNIT_TYPES,
    ArtifactReference,
    IndependentUnitType,
    PopulationCubeRow,
    PopulationManifest,
    PopulationObservedRate,
    PopulationOutcomeSummary,
    PopulationPurpose,
    RoutingDisposition,
)
from news_scalping_lab.memory.index import (
    PopulationCellMember,
    ProductionMemoryIndex,
)
from news_scalping_lab.memory.statistics import (
    BLOCK_BOOTSTRAP_VERSION,
    POPULATION_STATISTICS_VERSION,
    UnitObservation,
    aggregate_unit_label,
    count_labels,
    effective_sample_size,
    observed_rate,
    time_slices,
)
from news_scalping_lab.records.models import CANDIDATE_ERROR_RECORD_TYPES
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

POPULATION_CUBE_VERSION = "population_cube.v1"
POPULATION_SELECTION_BUDGET_VERSION = "population_selection_budget.v1"
POPULATION_MAX_SELECTED_RECORDS = 50_000
POPULATION_MAX_CUBE_ROWS = 250_000
POPULATION_PURPOSE_CLASSIFIER_VERSION = "population_purpose.v1"
_NEWSLESS_RECORD_TYPES = ("newsless_or_unexplained_case",)
_LEADER_SELECTION_RECORD_TYPES = (
    "blind_leader_preference_pair",
    "leader_preference_pair",
)
_PURPOSE_RECORD_FILTERS: dict[
    str,
    tuple[tuple[str, ...] | None, tuple[str, ...]],
] = {
    "catalyst_response": (
        None,
        tuple(
            sorted(
                set(CANDIDATE_ERROR_RECORD_TYPES)
                | set(_NEWSLESS_RECORD_TYPES)
                | set(_LEADER_SELECTION_RECORD_TYPES)
            )
        ),
    ),
    "candidate_error": (tuple(sorted(CANDIDATE_ERROR_RECORD_TYPES)), ()),
    "newsless": (_NEWSLESS_RECORD_TYPES, ()),
    "leader_selection": (_LEADER_SELECTION_RECORD_TYPES, ()),
}
POPULATION_ARTIFACT_ROOT = Path("runs/populations")
POPULATION_MEMBER_FILE = "member_records.jsonl"
POPULATION_UNIT_FILE = "independent_units.jsonl"
POPULATION_CUBE_FILE = "population_cube.jsonl"
POPULATION_MANIFEST_FILE = "population_manifest.json"

_INDEPENDENT_UNIT_TYPES = {
    "event-issuer-day",
    "issuer-day",
    "theme-day",
    "theme-day-ticker-day",
    "theme-day-pair",
    "ticker-day",
}
_ROUTING_DISPOSITIONS = {"REASONING", "CONTEXT", "AUDIT", "QUARANTINED"}
_RATE_METRICS = (
    "upper_limit_touched",
    "high_return_5",
    "high_return_10",
    "high_return_20",
)


@dataclass(frozen=True)
class PopulationBuildResult:
    manifest: PopulationManifest
    manifest_path: Path


@dataclass(frozen=True)
class _PopulationComputation:
    member_rows: list[dict[str, Any]]
    units: list[UnitObservation]
    unit_rows: list[dict[str, Any]]
    cube_rows: list[PopulationCubeRow]
    outcome_summary: PopulationOutcomeSummary
    observed_rates: list[PopulationObservedRate]
    effective_sample_size: float
    polarity_counts: dict[str, int]
    eligibility_counts: dict[str, int]
    label_quality_counts: dict[str, int]
    time_slice_counts: dict[str, int]
    regime_counts: dict[str, int]


class PopulationRetriever:
    """Build immutable population manifests from full selected-cell membership."""

    def __init__(self, root: Path, *, memory_index: ProductionMemoryIndex) -> None:
        self.root = root.resolve()
        self.memory_index = memory_index

    def build(
        self,
        *,
        run_id: str,
        cluster_id: str,
        cutoff_at: datetime,
        selected_cell_ids: list[str],
        independent_unit_type: IndependentUnitType,
        population_purpose: PopulationPurpose = "catalyst_response",
        routing_dispositions: tuple[RoutingDisposition, ...] = ("REASONING",),
        query_regime_cluster: str | None = None,
    ) -> PopulationBuildResult:
        safe_run_id = _safe_segment(run_id, field="run_id")
        safe_cluster_id = _safe_segment(cluster_id, field="cluster_id")
        request = _normalized_request(
            cutoff_at=cutoff_at,
            selected_cell_ids=selected_cell_ids,
            independent_unit_type=independent_unit_type,
            population_purpose=population_purpose,
            routing_dispositions=routing_dispositions,
            query_regime_cluster=query_regime_cluster,
        )
        snapshot, members = self.memory_index.population_members_for_cells(
            list(request.selected_cell_ids),
            cutoff_at=cutoff_at,
            independent_unit_type=independent_unit_type,
            routing_dispositions=tuple(request.routing_dispositions),
            included_memory_lanes=request.included_memory_lanes,
            included_record_types=_PURPOSE_RECORD_FILTERS[
                request.population_purpose
            ][0],
            excluded_record_types=_PURPOSE_RECORD_FILTERS[
                request.population_purpose
            ][1],
            max_records=POPULATION_MAX_SELECTED_RECORDS,
        )
        if not members:
            raise ValueError("selected cells contain no records for the requested unit type")
        members = _members_for_purpose(
            members,
            population_purpose=request.population_purpose,
            included_memory_lanes=request.included_memory_lanes,
        )
        if not members:
            raise ValueError("selected cells contain no records for the requested population purpose")
        identity = {
            **request.identity_payload(),
            "run_id": run_id,
            "cluster_id": cluster_id,
            "memory_snapshot_id": snapshot.snapshot_id,
            "source_generation_sha256": snapshot.source_generation_sha256,
            "corpus_manifest_sha256": snapshot.corpus_manifest_sha256,
            "statistics_version": POPULATION_STATISTICS_VERSION,
            "cube_version": POPULATION_CUBE_VERSION,
            "selection_budget_version": POPULATION_SELECTION_BUDGET_VERSION,
            "max_selected_record_count": POPULATION_MAX_SELECTED_RECORDS,
            "max_cube_row_count": POPULATION_MAX_CUBE_ROWS,
            "purpose_classifier_version": POPULATION_PURPOSE_CLASSIFIER_VERSION,
            "purpose_record_types_sha256": _purpose_record_types_sha256(
                request.population_purpose
            ),
            "bootstrap_version": BLOCK_BOOTSTRAP_VERSION,
        }
        population_id = "POP-" + sha256_text(canonical_json(identity))[:20].upper()
        computation = _compute_population(
            members,
            cutoff_at=cutoff_at,
            query_regime_cluster=request.query_regime_cluster,
            seed=_statistics_seed(members, cutoff_at=cutoff_at),
        )
        population_dir = (
            self.root
            / POPULATION_ARTIFACT_ROOT
            / safe_run_id
            / safe_cluster_id
            / population_id
        )
        try:
            population_dir.resolve().relative_to(
                (self.root / POPULATION_ARTIFACT_ROOT).resolve()
            )
        except ValueError as exc:
            raise ValueError("population artifact path escapes its root") from exc
        member_bytes = _jsonl_bytes(computation.member_rows)
        unit_bytes = _jsonl_bytes(computation.unit_rows)
        if len(computation.cube_rows) > POPULATION_MAX_CUBE_ROWS:
            raise ValueError(
                "population cube exceeds the operational row budget: "
                f"{len(computation.cube_rows)} > {POPULATION_MAX_CUBE_ROWS}"
            )
        cube_bytes = _jsonl_bytes(
            [row.model_dump(mode="json") for row in computation.cube_rows]
        )
        member_path = population_dir / POPULATION_MEMBER_FILE
        unit_path = population_dir / POPULATION_UNIT_FILE
        cube_path = population_dir / POPULATION_CUBE_FILE
        manifest_path = population_dir / POPULATION_MANIFEST_FILE
        manifest = PopulationManifest(
            population_id=population_id,
            run_id=run_id,
            cluster_id=cluster_id,
            cutoff_at=as_kst(cutoff_at),
            memory_snapshot_id=snapshot.snapshot_id,
            source_generation_sha256=snapshot.source_generation_sha256,
            corpus_manifest_sha256=snapshot.corpus_manifest_sha256,
            statistics_version=POPULATION_STATISTICS_VERSION,
            cube_version=POPULATION_CUBE_VERSION,
            selection_budget_version=POPULATION_SELECTION_BUDGET_VERSION,
            max_selected_record_count=POPULATION_MAX_SELECTED_RECORDS,
            max_cube_row_count=POPULATION_MAX_CUBE_ROWS,
            purpose_classifier_version=POPULATION_PURPOSE_CLASSIFIER_VERSION,
            purpose_record_types_sha256=_purpose_record_types_sha256(
                request.population_purpose
            ),
            selected_cell_ids=list(request.selected_cell_ids),
            routing_dispositions=list(request.routing_dispositions),
            membership_manifest_sha256=sha256_text(member_bytes.decode("utf-8")),
            independent_unit_type=independent_unit_type,
            population_purpose=request.population_purpose,
            included_memory_lanes=list(request.included_memory_lanes),
            query_regime_cluster=request.query_regime_cluster,
            raw_record_count=len(computation.member_rows),
            independent_unit_count=len(computation.units),
            effective_sample_size=computation.effective_sample_size,
            polarity_counts=computation.polarity_counts,
            eligibility_counts=computation.eligibility_counts,
            label_quality_counts=computation.label_quality_counts,
            time_slice_counts=computation.time_slice_counts,
            regime_counts=computation.regime_counts,
            outcome_summary=computation.outcome_summary,
            observed_rates=computation.observed_rates,
            member_records=_artifact_reference(self.root, member_path, member_bytes),
            independent_units=_artifact_reference(self.root, unit_path, unit_bytes),
            cube_rows=_artifact_reference(self.root, cube_path, cube_bytes),
        )
        _write_immutable_bytes(member_path, member_bytes)
        _write_immutable_bytes(unit_path, unit_bytes)
        _write_immutable_bytes(cube_path, cube_bytes)
        _write_immutable_manifest(manifest_path, manifest)
        inspection = self.inspect(
            manifest_path,
            force_database_verification=False,
        )
        if not inspection["passed"]:
            raise ValueError(
                "population manifest failed self-inspection: "
                + ", ".join(inspection["errors"])
            )
        return PopulationBuildResult(manifest=manifest, manifest_path=manifest_path)

    def inspect(
        self,
        manifest_path: Path,
        *,
        force_database_verification: bool = True,
    ) -> dict[str, Any]:
        path = manifest_path.resolve()
        base: dict[str, Any] = {
            "manifest_path": relative_to_root(path, self.root),
            "passed": False,
            "errors": [],
        }
        try:
            manifest = PopulationManifest.model_validate(read_json(path))
        except (OSError, ValueError) as exc:
            return {**base, "errors": [f"population_manifest_invalid:{exc}"]}
        errors: list[str] = []
        artifact_rows: dict[str, list[dict[str, Any]]] = {}
        for name, artifact in (
            ("member_records", manifest.member_records),
            ("independent_units", manifest.independent_units),
            ("cube_rows", manifest.cube_rows),
        ):
            artifact_path = (self.root / artifact.artifact_path).resolve()
            try:
                artifact_path.relative_to(path.parent)
            except ValueError:
                errors.append(f"{name}_path_escapes_population")
                continue
            if not artifact_path.exists():
                errors.append(f"{name}_missing")
                continue
            if file_sha256(artifact_path) != artifact.sha256:
                errors.append(f"{name}_hash_mismatch")
            try:
                rows = _read_jsonl(artifact_path)
            except (OSError, ValueError):
                errors.append(f"{name}_invalid")
                continue
            artifact_rows[name] = rows
            if len(rows) != artifact.item_count:
                errors.append(f"{name}_count_mismatch")
        try:
            snapshot, members = self.memory_index.population_members_for_cells(
                manifest.selected_cell_ids,
                cutoff_at=manifest.cutoff_at,
                independent_unit_type=manifest.independent_unit_type,
                routing_dispositions=tuple(manifest.routing_dispositions),
                included_memory_lanes=tuple(manifest.included_memory_lanes),
                included_record_types=_PURPOSE_RECORD_FILTERS[
                    manifest.population_purpose
                ][0],
                excluded_record_types=_PURPOSE_RECORD_FILTERS[
                    manifest.population_purpose
                ][1],
                max_records=POPULATION_MAX_SELECTED_RECORDS,
                force_database_verification=force_database_verification,
            )
            members = _members_for_purpose(
                members,
                population_purpose=manifest.population_purpose,
                included_memory_lanes=tuple(manifest.included_memory_lanes),
            )
            if not members:
                raise ValueError(
                    "selected cells contain no records for the requested population purpose"
                )
            recomputed = _compute_population(
                members,
                cutoff_at=manifest.cutoff_at,
                query_regime_cluster=manifest.query_regime_cluster,
                seed=_statistics_seed(members, cutoff_at=manifest.cutoff_at),
            )
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"population_recompute_failed:{exc}")
        else:
            if manifest.statistics_version != POPULATION_STATISTICS_VERSION:
                errors.append("population_statistics_version_mismatch")
            if manifest.cube_version != POPULATION_CUBE_VERSION:
                errors.append("population_cube_version_mismatch")
            if manifest.bootstrap_version != BLOCK_BOOTSTRAP_VERSION:
                errors.append("population_bootstrap_version_mismatch")
            if (
                manifest.selection_budget_version
                != POPULATION_SELECTION_BUDGET_VERSION
                or manifest.max_selected_record_count
                != POPULATION_MAX_SELECTED_RECORDS
                or manifest.max_cube_row_count != POPULATION_MAX_CUBE_ROWS
            ):
                errors.append("population_selection_budget_mismatch")
            if (
                manifest.purpose_classifier_version
                != POPULATION_PURPOSE_CLASSIFIER_VERSION
                or manifest.purpose_record_types_sha256
                != _purpose_record_types_sha256(manifest.population_purpose)
            ):
                errors.append("population_purpose_classifier_mismatch")
            expected_identity = {
                "cutoff_at": as_kst(manifest.cutoff_at).isoformat(),
                "selected_cell_ids": sorted(manifest.selected_cell_ids),
                "independent_unit_type": manifest.independent_unit_type,
                "population_purpose": manifest.population_purpose,
                "included_memory_lanes": sorted(manifest.included_memory_lanes),
                "routing_dispositions": sorted(manifest.routing_dispositions),
                "query_regime_cluster": manifest.query_regime_cluster,
                "run_id": manifest.run_id,
                "cluster_id": manifest.cluster_id,
                "memory_snapshot_id": snapshot.snapshot_id,
                "source_generation_sha256": snapshot.source_generation_sha256,
                "corpus_manifest_sha256": snapshot.corpus_manifest_sha256,
                "statistics_version": POPULATION_STATISTICS_VERSION,
                "cube_version": POPULATION_CUBE_VERSION,
                "selection_budget_version": POPULATION_SELECTION_BUDGET_VERSION,
                "max_selected_record_count": POPULATION_MAX_SELECTED_RECORDS,
                "max_cube_row_count": POPULATION_MAX_CUBE_ROWS,
                "purpose_classifier_version": POPULATION_PURPOSE_CLASSIFIER_VERSION,
                "purpose_record_types_sha256": _purpose_record_types_sha256(
                    manifest.population_purpose
                ),
                "bootstrap_version": BLOCK_BOOTSTRAP_VERSION,
            }
            expected_population_id = (
                "POP-" + sha256_text(canonical_json(expected_identity))[:20].upper()
            )
            if expected_population_id != manifest.population_id:
                errors.append("population_id_mismatch")
            if path.parent.name != manifest.population_id:
                errors.append("population_directory_identity_mismatch")
            if path.parent.parent.name != manifest.cluster_id:
                errors.append("population_cluster_directory_mismatch")
            if path.parent.parent.parent.name != manifest.run_id:
                errors.append("population_run_directory_mismatch")
            expected_manifest_path = (
                self.root
                / POPULATION_ARTIFACT_ROOT
                / _safe_segment(manifest.run_id, field="run_id")
                / _safe_segment(manifest.cluster_id, field="cluster_id")
                / manifest.population_id
                / POPULATION_MANIFEST_FILE
            ).resolve()
            if path != expected_manifest_path:
                errors.append("population_manifest_path_mismatch")
            expected_artifact_names = {
                "member_records": POPULATION_MEMBER_FILE,
                "independent_units": POPULATION_UNIT_FILE,
                "cube_rows": POPULATION_CUBE_FILE,
            }
            for artifact_name, filename in expected_artifact_names.items():
                artifact = getattr(manifest, artifact_name)
                expected_artifact_path = (path.parent / filename).resolve()
                if (self.root / artifact.artifact_path).resolve() != expected_artifact_path:
                    errors.append(f"{artifact_name}_filename_mismatch")
            if snapshot.snapshot_id != manifest.memory_snapshot_id:
                errors.append("memory_snapshot_id_mismatch")
            if snapshot.source_generation_sha256 != manifest.source_generation_sha256:
                errors.append("source_generation_mismatch")
            if snapshot.corpus_manifest_sha256 != manifest.corpus_manifest_sha256:
                errors.append("corpus_manifest_mismatch")
            expected_rows = {
                "member_records": recomputed.member_rows,
                "independent_units": recomputed.unit_rows,
                "cube_rows": [row.model_dump(mode="json") for row in recomputed.cube_rows],
            }
            for name, rows in expected_rows.items():
                if artifact_rows.get(name) != rows:
                    errors.append(f"{name}_recompute_mismatch")
            expected_summary = _summary_projection(recomputed)
            observed_summary = _manifest_summary_projection(manifest)
            if expected_summary != observed_summary:
                errors.append("population_summary_recompute_mismatch")
        return {
            **base,
            "passed": not errors,
            "errors": errors,
            "population_id": manifest.population_id,
            "memory_snapshot_id": manifest.memory_snapshot_id,
            "raw_record_count": manifest.raw_record_count,
            "independent_unit_count": manifest.independent_unit_count,
        }


@dataclass(frozen=True)
class _PopulationRequest:
    cutoff_at: str
    selected_cell_ids: tuple[str, ...]
    independent_unit_type: IndependentUnitType
    population_purpose: PopulationPurpose
    included_memory_lanes: tuple[str, ...]
    routing_dispositions: tuple[RoutingDisposition, ...]
    query_regime_cluster: str | None

    def identity_payload(self) -> dict[str, Any]:
        return {
            "cutoff_at": self.cutoff_at,
            "selected_cell_ids": list(self.selected_cell_ids),
            "independent_unit_type": self.independent_unit_type,
            "population_purpose": self.population_purpose,
            "included_memory_lanes": list(self.included_memory_lanes),
            "routing_dispositions": list(self.routing_dispositions),
            "query_regime_cluster": self.query_regime_cluster,
        }


def _normalized_request(
    *,
    cutoff_at: datetime,
    selected_cell_ids: list[str],
    independent_unit_type: str,
    population_purpose: str,
    routing_dispositions: tuple[str, ...],
    query_regime_cluster: str | None,
) -> _PopulationRequest:
    cells = sorted({cell_id.strip() for cell_id in selected_cell_ids if cell_id.strip()})
    dispositions = sorted({item.strip().upper() for item in routing_dispositions if item.strip()})
    if not cells:
        raise ValueError("population retrieval requires at least one cell")
    if independent_unit_type not in _INDEPENDENT_UNIT_TYPES:
        raise ValueError("unsupported independent unit type")
    normalized_purpose = population_purpose.strip().lower()
    if normalized_purpose not in POPULATION_PURPOSE_LANES:
        raise ValueError("unsupported population purpose")
    if independent_unit_type not in POPULATION_PURPOSE_UNIT_TYPES[normalized_purpose]:
        raise ValueError("population purpose is incompatible with independent unit type")
    if not dispositions or any(item not in _ROUTING_DISPOSITIONS for item in dispositions):
        raise ValueError("unsupported routing disposition")
    if dispositions != ["REASONING"]:
        raise ValueError("observed populations require REASONING disposition only")
    regime = query_regime_cluster.strip().upper() if query_regime_cluster else None
    return _PopulationRequest(
        cutoff_at=as_kst(cutoff_at).isoformat(),
        selected_cell_ids=tuple(cells),
        independent_unit_type=cast(IndependentUnitType, independent_unit_type),
        population_purpose=cast(PopulationPurpose, normalized_purpose),
        included_memory_lanes=tuple(
            sorted(POPULATION_PURPOSE_LANES[normalized_purpose])
        ),
        routing_dispositions=cast(tuple[RoutingDisposition, ...], tuple(dispositions)),
        query_regime_cluster=regime,
    )


def _members_for_purpose(
    members: list[PopulationCellMember],
    *,
    population_purpose: str,
    included_memory_lanes: tuple[str, ...],
) -> list[PopulationCellMember]:
    allowed = set(included_memory_lanes)
    return [
        member
        for member in members
        if _record_matches_purpose(member, population_purpose)
        and allowed.intersection(member.memory_lanes)
    ]


def _record_matches_purpose(
    member: PopulationCellMember,
    population_purpose: str,
) -> bool:
    record_type = member.record_type.lower()
    included, excluded = _PURPOSE_RECORD_FILTERS[population_purpose]
    return (included is None or record_type in included) and record_type not in excluded


def _purpose_record_types_sha256(population_purpose: str) -> str:
    included, excluded = _PURPOSE_RECORD_FILTERS[population_purpose]
    return sha256_text(
        canonical_json(
            {
                "version": POPULATION_PURPOSE_CLASSIFIER_VERSION,
                "purpose": population_purpose,
                "included": list(included) if included is not None else None,
                "excluded": list(excluded),
            }
        )
    )


def _compute_population(
    members: list[PopulationCellMember],
    *,
    cutoff_at: datetime,
    query_regime_cluster: str | None,
    seed: int,
) -> _PopulationComputation:
    member_rows = [_member_row(member) for member in members]
    grouped: dict[str, list[PopulationCellMember]] = defaultdict(list)
    for member in members:
        grouped[member.independent_unit_id].append(member)
    units = [
        _aggregate_unit(unit_id, grouped[unit_id]) for unit_id in sorted(grouped)
    ]
    unit_rows = [_unit_row(unit) for unit in units]
    outcome_summary = _outcome_summary(units)
    rates = [
        _rate_contract(observed_rate(units, metric=metric, seed=seed + index))
        for index, metric in enumerate(_RATE_METRICS)
    ]
    cube_rows = _build_cube(
        members,
        units,
        cutoff_date=as_kst(cutoff_at).date(),
        query_regime_cluster=query_regime_cluster,
    )
    all_slices = [
        time_slice
        for unit in units
        for time_slice in _unit_time_slices(
            unit,
            cutoff_date=as_kst(cutoff_at).date(),
            query_regime_cluster=query_regime_cluster,
        )
    ]
    return _PopulationComputation(
        member_rows=member_rows,
        units=units,
        unit_rows=unit_rows,
        cube_rows=cube_rows,
        outcome_summary=outcome_summary,
        observed_rates=rates,
        effective_sample_size=effective_sample_size(
            unit.sample_weight for unit in units
        ),
        polarity_counts=count_labels(units, "polarity"),
        eligibility_counts=count_labels(units, "eligibility"),
        label_quality_counts=count_labels(units, "label_quality"),
        time_slice_counts=_count_strings(all_slices),
        regime_counts=_count_strings(
            regime for unit in units for regime in unit.regime_clusters
        ),
    )


def _statistics_seed(
    members: list[PopulationCellMember],
    *,
    cutoff_at: datetime,
) -> int:
    payload = {
        "cutoff_at": as_kst(cutoff_at).isoformat(),
        "statistics_version": POPULATION_STATISTICS_VERSION,
        "bootstrap_version": BLOCK_BOOTSTRAP_VERSION,
        "members_sha256": sha256_text(
            canonical_json([_member_row(member) for member in members])
        ),
    }
    return int(hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()[:16], 16)


def _aggregate_unit(
    independent_unit_id: str,
    members: list[PopulationCellMember],
) -> UnitObservation:
    trade_dates = {member.trade_date for member in members}
    if len(trade_dates) != 1:
        raise ValueError(f"independent unit spans trade dates: {independent_unit_id}")
    eligibility_values = {member.training_eligible for member in members}
    eligibility = (
        "ELIGIBLE"
        if eligibility_values == {True}
        else "INELIGIBLE"
        if eligibility_values == {False}
        else "MIXED"
    )
    high_return, high_status = _aggregate_unit_float(
        members,
        value_field="high_return_pct",
        status_field="high_return_status",
    )
    close_return, close_status = _aggregate_unit_float(
        members,
        value_field="close_return_pct",
        status_field="close_return_status",
    )
    upper_limit, upper_status = _aggregate_unit_bool(
        members,
        value_field="upper_limit_touched",
        status_field="upper_limit_status",
    )
    sample_weight, weight_status = _aggregate_unit_weight(members)
    regime = aggregate_unit_label(
        (member.regime_cluster for member in members),
        missing="UNKNOWN",
    )
    return UnitObservation(
        independent_unit_id=independent_unit_id,
        trade_date=trade_dates.pop(),
        record_ids=tuple(sorted({member.record_id for member in members})),
        cell_ids=tuple(
            sorted({cell_id for member in members for cell_id in member.matched_cell_ids})
        ),
        memory_lanes=tuple(
            sorted({lane for member in members for lane in member.memory_lanes})
        ),
        record_types=tuple(sorted({member.record_type for member in members})),
        path_types=tuple(sorted({member.path_type for member in members})),
        regime_clusters=(regime,),
        polarity=aggregate_unit_label(
            (member.evidence_polarity for member in members),
            missing="UNKNOWN",
        ),
        eligibility=eligibility,
        label_quality=aggregate_unit_label(
            (member.label_quality for member in members),
            missing="missing",
        ),
        sample_weight=sample_weight,
        high_return_pct=high_return,
        close_return_pct=close_return,
        upper_limit_touched=upper_limit,
        high_return_status=high_status,
        close_return_status=close_status,
        upper_limit_status=upper_status,
        sample_weight_status=weight_status,
    )


def _aggregate_unit_float(
    members: list[PopulationCellMember],
    *,
    value_field: str,
    status_field: str,
) -> tuple[float | None, str]:
    statuses = {str(getattr(member, status_field)) for member in members}
    if "INVALID_CONFLICT" in statuses:
        return None, "INVALID_CONFLICT"
    values = [
        float(value)
        for member in members
        if (value := getattr(member, value_field)) is not None
    ]
    if not values:
        return None, "MISSING"
    if max(values) - min(values) > 1e-9:
        return None, "INVALID_CONFLICT"
    return values[0], "VALID"


def _aggregate_unit_bool(
    members: list[PopulationCellMember],
    *,
    value_field: str,
    status_field: str,
) -> tuple[bool | None, str]:
    statuses = {str(getattr(member, status_field)) for member in members}
    if "INVALID_CONFLICT" in statuses:
        return None, "INVALID_CONFLICT"
    values = {
        bool(value)
        for member in members
        if (value := getattr(member, value_field)) is not None
    }
    if not values:
        return None, "MISSING"
    if len(values) != 1:
        return None, "INVALID_CONFLICT"
    return values.pop(), "VALID"


def _aggregate_unit_weight(
    members: list[PopulationCellMember],
) -> tuple[float, str]:
    statuses = {member.sample_weight_status for member in members}
    values = [member.sample_weight for member in members]
    if (
        "INVALID_CONFLICT" in statuses
        or any(value <= 0.0 or not math.isfinite(value) for value in values)
        or not math.isclose(max(values), min(values), rel_tol=1e-5, abs_tol=1e-5)
    ):
        return 0.0, "INVALID_CONFLICT"
    return min(values), "VALID" if "VALID" in statuses else "DEFAULT"


def _build_cube(
    members: list[PopulationCellMember],
    units: list[UnitObservation],
    *,
    cutoff_date: date,
    query_regime_cluster: str | None,
    max_rows: int = POPULATION_MAX_CUBE_ROWS,
) -> list[PopulationCubeRow]:
    units_by_id = {unit.independent_unit_id: unit for unit in units}
    grouped: dict[
        tuple[str, ...],
        dict[str, set[str]],
    ] = defaultdict(lambda: defaultdict(set))
    for member in members:
        unit = units_by_id[member.independent_unit_id]
        lanes = member.memory_lanes or ("NO_MEMORY_LANE",)
        for cell_id in member.matched_cell_ids:
            for lane in lanes:
                for time_slice in _unit_time_slices(
                    unit,
                    cutoff_date=cutoff_date,
                    query_regime_cluster=query_regime_cluster,
                ):
                    cube_key = (
                        cell_id,
                        lane,
                        time_slice,
                        unit.regime_clusters[0],
                        member.record_type,
                        member.path_type,
                        member.label_quality,
                    )
                    if cube_key not in grouped and len(grouped) >= max_rows:
                        raise ValueError(
                            "population cube exceeds the operational row budget: "
                            f"> {max_rows}"
                        )
                    grouped[cube_key][member.independent_unit_id].add(
                        member.record_id
                    )
    rows: list[PopulationCubeRow] = []
    for row_key in sorted(grouped):
        cell_id, lane, time_slice, regime, record_type, path_type, quality = row_key
        records_by_unit = grouped[row_key]
        unit_ids = sorted(records_by_unit)
        row_units = [units_by_id[unit_id] for unit_id in unit_ids]
        record_ids = sorted(
            {
                record_id
                for unit_record_ids in records_by_unit.values()
                for record_id in unit_record_ids
            }
        )
        rows.append(
            PopulationCubeRow(
                cell_id=cell_id,
                memory_lane=lane,
                time_slice=time_slice,
                regime_cluster=regime,
                record_type=record_type,
                path_type=path_type,
                label_quality=quality,
                raw_record_count=len(record_ids),
                independent_unit_count=len(unit_ids),
                effective_sample_size=effective_sample_size(
                    unit.sample_weight for unit in row_units
                ),
                polarity_counts=count_labels(row_units, "polarity"),
                outcome_summary=_outcome_summary(row_units),
                member_record_ids_sha256=sha256_text(canonical_json(record_ids)),
                independent_unit_ids_sha256=sha256_text(canonical_json(unit_ids)),
            )
        )
    return rows


def _unit_time_slices(
    unit: UnitObservation,
    *,
    cutoff_date: date,
    query_regime_cluster: str | None,
) -> tuple[str, ...]:
    values = list(time_slices(unit.trade_date, cutoff_date=cutoff_date))
    if query_regime_cluster and query_regime_cluster in unit.regime_clusters:
        values.append("SIMILAR_REGIME")
    return tuple(values)


def _outcome_summary(units: list[UnitObservation]) -> PopulationOutcomeSummary:
    observed = [unit for unit in units if unit.outcome_observed]
    high_values = [
        (unit.high_return_pct, unit.sample_weight)
        for unit in units
        if unit.high_return_pct is not None and unit.sample_weight > 0.0
    ]
    close_values = [
        (unit.close_return_pct, unit.sample_weight)
        for unit in units
        if unit.close_return_pct is not None and unit.sample_weight > 0.0
    ]
    upper_ids = {
        unit.independent_unit_id
        for unit in units
        if unit.sample_weight > 0.0 and unit.upper_limit_touched is True
    }
    def threshold_count(threshold: float) -> int:
        return sum(
            1
            for unit in units
            if unit.sample_weight > 0.0
            and unit.high_return_pct is not None
            and unit.high_return_pct >= threshold
        )

    return PopulationOutcomeSummary(
        observed_unit_count=len(observed),
        missing_outcome_unit_count=len(units) - len(observed),
        upper_limit_touched_count=len(upper_ids),
        high_return_5_count=threshold_count(5.0),
        high_return_10_count=threshold_count(10.0),
        high_return_20_count=threshold_count(20.0),
        mean_high_return_pct=_weighted_mean(high_values),
        median_high_return_pct=_weighted_percentile(high_values, 0.50),
        p10_high_return_pct=_weighted_percentile(high_values, 0.10),
        p25_high_return_pct=_weighted_percentile(high_values, 0.25),
        p75_high_return_pct=_weighted_percentile(high_values, 0.75),
        p90_high_return_pct=_weighted_percentile(high_values, 0.90),
        mean_close_return_pct=_weighted_mean(close_values),
        median_close_return_pct=_weighted_percentile(close_values, 0.50),
        p10_close_return_pct=_weighted_percentile(close_values, 0.10),
        p25_close_return_pct=_weighted_percentile(close_values, 0.25),
        p75_close_return_pct=_weighted_percentile(close_values, 0.75),
        p90_close_return_pct=_weighted_percentile(close_values, 0.90),
    )


def _weighted_mean(values: list[tuple[float, float]]) -> float | None:
    if not values:
        return None
    denominator = sum(weight for _value, weight in values)
    return sum(value * weight for value, weight in values) / denominator


def _weighted_percentile(
    values: list[tuple[float, float]],
    quantile: float,
) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    total_weight = sum(weight for _value, weight in ordered)
    threshold = quantile * total_weight
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def _rate_contract(rate: Any) -> PopulationObservedRate:
    return PopulationObservedRate(
        metric=rate.metric,
        numerator=rate.numerator,
        denominator=rate.denominator,
        weighted_numerator=rate.weighted_numerator,
        weighted_denominator=rate.weighted_denominator,
        observed_population_rate=rate.observed_rate,
        lower_bound=rate.lower_bound,
        upper_bound=rate.upper_bound,
        bootstrap_iterations=rate.bootstrap_iterations,
    )


def _member_row(member: PopulationCellMember) -> dict[str, Any]:
    payload = asdict(member)
    payload["trade_date"] = member.trade_date.isoformat()
    payload["matched_cell_ids"] = list(member.matched_cell_ids)
    payload["memory_lanes"] = list(member.memory_lanes)
    return payload


def _unit_row(unit: UnitObservation) -> dict[str, Any]:
    payload = asdict(unit)
    payload["trade_date"] = unit.trade_date.isoformat()
    for field in (
        "record_ids",
        "cell_ids",
        "memory_lanes",
        "record_types",
        "path_types",
        "regime_clusters",
    ):
        payload[field] = list(payload[field])
    payload["outcome_observed"] = unit.outcome_observed
    return payload


def _summary_projection(computation: _PopulationComputation) -> dict[str, Any]:
    return {
        "raw_record_count": len(computation.member_rows),
        "independent_unit_count": len(computation.units),
        "effective_sample_size": computation.effective_sample_size,
        "polarity_counts": computation.polarity_counts,
        "eligibility_counts": computation.eligibility_counts,
        "label_quality_counts": computation.label_quality_counts,
        "time_slice_counts": computation.time_slice_counts,
        "regime_counts": computation.regime_counts,
        "outcome_summary": computation.outcome_summary.model_dump(mode="json"),
        "observed_rates": [item.model_dump(mode="json") for item in computation.observed_rates],
        "membership_manifest_sha256": sha256_text(
            _jsonl_bytes(computation.member_rows).decode("utf-8")
        ),
    }


def _manifest_summary_projection(manifest: PopulationManifest) -> dict[str, Any]:
    return {
        "raw_record_count": manifest.raw_record_count,
        "independent_unit_count": manifest.independent_unit_count,
        "effective_sample_size": manifest.effective_sample_size,
        "polarity_counts": manifest.polarity_counts,
        "eligibility_counts": manifest.eligibility_counts,
        "label_quality_counts": manifest.label_quality_counts,
        "time_slice_counts": manifest.time_slice_counts,
        "regime_counts": manifest.regime_counts,
        "outcome_summary": manifest.outcome_summary.model_dump(mode="json"),
        "observed_rates": [item.model_dump(mode="json") for item in manifest.observed_rates],
        "membership_manifest_sha256": manifest.membership_manifest_sha256,
    }


def _artifact_reference(root: Path, path: Path, payload: bytes) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative_to_root(path, root),
        sha256=hashlib.sha256(payload).hexdigest(),
        item_count=payload.count(b"\n"),
    )


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = read_json_line(line)
        if not isinstance(value, dict):
            raise ValueError("population JSONL rows must be objects")
        rows.append(value)
    return rows


def read_json_line(line: str) -> Any:
    return json.loads(line)


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable population artifact conflict: {path.name}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_immutable_manifest(path: Path, manifest: PopulationManifest) -> None:
    if path.exists():
        existing = PopulationManifest.model_validate(read_json(path))
        if existing != manifest:
            raise ValueError("immutable population manifest conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    write_json(temporary, manifest.model_dump(mode="json"))
    os.replace(temporary, path)


def _safe_segment(value: str, *, field: str) -> str:
    stripped = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    if (
        not stripped
        or value != stripped
        or stripped in {".", ".."}
        or stripped.endswith((".", " "))
        or stripped.split(".", 1)[0].upper() in reserved
        or any(character not in allowed for character in stripped)
    ):
        raise ValueError(f"{field} contains unsafe path characters")
    return stripped


def _count_strings(values: Any) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for value in values:
        counts[str(value)] += 1
    return dict(sorted(counts.items()))
