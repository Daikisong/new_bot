"""Deterministic strata-aware representative selection over population artifacts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    PopulationManifest,
    RepresentativeRecord,
    RepresentativeSetManifest,
    RepresentativeStratum,
)
from news_scalping_lab.llm.base import conservative_token_upper_bound
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    RepresentativeSourceRecord,
)
from news_scalping_lab.memory.population import (
    PopulationRetriever,
    _inspect_built_population,
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

REPRESENTATIVE_SELECTION_VERSION = "stratified_mmr_facility.v3"
REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION = (
    "stratified_mmr_facility.v3.quality_full_extended_pack"
)
REPRESENTATIVE_ARTIFACT_ROOT = Path("runs/representatives")
REPRESENTATIVE_RECORD_FILE = "representative_records.jsonl"
REPRESENTATIVE_MANIFEST_FILE = "representative_set_manifest.json"
REPRESENTATIVE_MAX_SELECTED_RECORDS = 32
REPRESENTATIVE_MAX_CANDIDATE_POOL = 512
REPRESENTATIVE_MAX_TOKEN_COUNT = 24_000
REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT = 48_000
REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION = 8
REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION = 4
REPRESENTATIVE_CONTEXT_EXCERPT_CHARS = 1_600
REPRESENTATIVE_INITIAL_MIN_RECORDS = 8
REPRESENTATIVE_INITIAL_MAX_RECORDS = 16
REPRESENTATIVE_FACILITY_ANCHOR_COUNT = 32
REPRESENTATIVE_MAX_STRATUM_RESERVE = 128
REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE = 0.25


class RepresentativeSelectionBudgetError(ValueError):
    """Raised when a valid population cannot fit the representative budgets."""


@dataclass(frozen=True)
class RepresentativeBuildResult:
    manifest: RepresentativeSetManifest
    manifest_path: Path


@dataclass(frozen=True)
class _Candidate:
    record_id: str
    independent_unit_id: str
    trade_date: date
    concentration_key: str
    strata: tuple[str, ...]
    record_label_quality: str = "missing"


@dataclass(frozen=True)
class _Selection:
    rows: list[RepresentativeRecord]
    candidate_pool_count: int
    target_selected_record_count: int
    population_strata: dict[str, set[str]]
    query_embedding_sha256: str


class RepresentativeSelector:
    """Select one cutoff-safe record per unit using quotas and embedding MMR."""

    def __init__(
        self,
        root: Path,
        *,
        memory_index: ProductionMemoryIndex,
    ) -> None:
        self.root = root.resolve()
        self.memory_index = memory_index
        self.population_retriever = PopulationRetriever(
            self.root,
            memory_index=memory_index,
        )

    def build(
        self,
        *,
        population_manifest_path: Path,
        query: str,
        query_vector: list[float] | None = None,
        allow_distribution_shortfall: bool = False,
    ) -> RepresentativeBuildResult:
        query_text = query.strip()
        if not query_text:
            raise ValueError("representative selection requires a query")
        population_path = population_manifest_path.resolve()
        population = self._validated_population(
            population_path,
            force_database_verification=False,
        )
        if query_vector is None:
            query_vectors = self.memory_index.embedding_provider.embed_texts(
                [query_text]
            )
            if len(query_vectors) != 1:
                raise ValueError(
                    "embedding provider returned the wrong query vector count"
                )
            query_vector = query_vectors[0]
        query_vector = _normalized_embedding_vector(
            query_vector,
            field="representative query",
        )
        query_embedding_sha256 = sha256_text(canonical_json(query_vector))
        selection_version = (
            REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION
            if allow_distribution_shortfall
            else REPRESENTATIVE_SELECTION_VERSION
        )
        max_token_count = (
            REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
            if allow_distribution_shortfall
            else REPRESENTATIVE_MAX_TOKEN_COUNT
        )
        identity = _representative_identity(
            population,
            population_path=population_path,
            query=query_text,
            embedding_model=self.memory_index.embedding_provider.embedding_method,
            query_embedding_sha256=query_embedding_sha256,
            selection_version=selection_version,
            max_token_count=max_token_count,
        )
        representative_set_id = "REP-" + sha256_text(canonical_json(identity))[
            :20
        ].upper()
        output_dir = (
            self.root
            / REPRESENTATIVE_ARTIFACT_ROOT
            / _safe_segment(population.run_id, field="run_id")
            / _safe_segment(population.cluster_id, field="cluster_id")
            / representative_set_id
        )
        _require_under(output_dir, self.root / REPRESENTATIVE_ARTIFACT_ROOT)
        records_path = output_dir / REPRESENTATIVE_RECORD_FILE
        manifest_path = output_dir / REPRESENTATIVE_MANIFEST_FILE
        cached_manifest = _load_cached_representative(
            self.root,
            manifest_path=manifest_path,
            expected_identity=identity,
        )
        if cached_manifest is not None:
            return RepresentativeBuildResult(
                manifest=cached_manifest,
                manifest_path=manifest_path,
            )
        selection = self._select(
            population,
            population_path=population_path,
            query=query_text,
            force_database_verification=False,
            query_vector=query_vector,
            allow_distribution_shortfall=allow_distribution_shortfall,
        )
        if selection.query_embedding_sha256 != query_embedding_sha256:
            raise ValueError("representative selection changed the query embedding identity")
        record_bytes = _jsonl_bytes(
            [row.model_dump(mode="json") for row in selection.rows]
        )
        selected_ids = [row.record_id for row in selection.rows]
        selected_units = [row.independent_unit_id for row in selection.rows]
        strata = _strata_contract(selection)
        covered_strata = sum(1 for item in strata if item.selected_unit_count)
        distribution_error = _max_distribution_share_error(
            strata,
            population_unit_count=population.independent_unit_count,
            selected_unit_count=len(selection.rows),
        )
        manifest = RepresentativeSetManifest(
            representative_set_id=representative_set_id,
            run_id=population.run_id,
            cluster_id=population.cluster_id,
            cutoff_at=population.cutoff_at,
            query_text=query_text,
            query_sha256=sha256_text(query_text),
            query_embedding_sha256=selection.query_embedding_sha256,
            population_id=population.population_id,
            population_manifest_sha256=file_sha256(population_path),
            memory_snapshot_id=population.memory_snapshot_id,
            source_generation_sha256=population.source_generation_sha256,
            corpus_manifest_sha256=population.corpus_manifest_sha256,
            selection_version=selection_version,
            embedding_model=self.memory_index.embedding_provider.embedding_method,
            candidate_pool_count=selection.candidate_pool_count,
            target_selected_record_count=selection.target_selected_record_count,
            population_record_count=population.raw_record_count,
            population_unit_count=population.independent_unit_count,
            selected_record_count=len(selection.rows),
            selected_unit_count=len(selection.rows),
            omitted_population_record_count=(
                population.raw_record_count - len(selection.rows)
            ),
            omitted_population_unit_count=(
                population.independent_unit_count - len(selection.rows)
            ),
            max_selected_record_count=REPRESENTATIVE_MAX_SELECTED_RECORDS,
            max_candidate_pool_count=REPRESENTATIVE_MAX_CANDIDATE_POOL,
            max_token_count=max_token_count,
            max_trade_date_concentration=(
                REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION
            ),
            max_unit_key_concentration=REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION,
            estimated_token_count=len(record_bytes),
            diversity_coverage_ratio=(
                covered_strata / len(strata) if strata else 0.0
            ),
            max_distribution_share_error=distribution_error,
            distribution_share_error_tolerance=(
                REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE
            ),
            strata=strata,
            selected_record_ids=selected_ids,
            selected_independent_unit_ids=selected_units,
            representative_records=_artifact_reference(
                self.root,
                records_path,
                record_bytes,
            ),
        )
        _write_immutable_bytes(records_path, record_bytes)
        _write_immutable_manifest(manifest_path, manifest)
        inspection = _inspect_built_representative(
            self.root,
            manifest_path=manifest_path,
            expected_manifest=manifest,
        )
        if inspection["passed"] is not True:
            raise ValueError(
                "representative set failed self-inspection: "
                + ", ".join(inspection["errors"])
            )
        return RepresentativeBuildResult(manifest=manifest, manifest_path=manifest_path)

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
            raw_manifest = read_json(path)
        except (OSError, ValueError) as exc:
            return {**base, "errors": [f"representative_manifest_invalid:{exc}"]}
        if (
            isinstance(raw_manifest, dict)
            and raw_manifest.get("schema_version")
            in {
                "nslab.representative_set_manifest.v1",
                "nslab.representative_set_manifest.v2",
            }
        ):
            return {
                **base,
                "errors": ["representative_manifest_schema_legacy"],
                "legacy_read_compatible": True,
            }
        try:
            manifest = RepresentativeSetManifest.model_validate(raw_manifest)
            expected_path = (
                self.root
                / REPRESENTATIVE_ARTIFACT_ROOT
                / _safe_segment(manifest.run_id, field="run_id")
                / _safe_segment(manifest.cluster_id, field="cluster_id")
                / _safe_segment(
                    manifest.representative_set_id,
                    field="representative_set_id",
                )
                / REPRESENTATIVE_MANIFEST_FILE
            ).resolve()
        except ValueError as exc:
            return {**base, "errors": [f"representative_manifest_invalid:{exc}"]}
        errors: list[str] = []
        if path != expected_path:
            errors.append("representative_manifest_path_mismatch")
        population_path = _population_manifest_path(self.root, manifest)
        try:
            population = self._validated_population(
                population_path,
                force_database_verification=force_database_verification,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            errors.append(f"representative_population_invalid:{exc}")
            population = None
        record_path = (self.root / manifest.representative_records.artifact_path).resolve()
        if record_path != path.parent / REPRESENTATIVE_RECORD_FILE:
            errors.append("representative_records_path_mismatch")
        if not record_path.exists():
            errors.append("representative_records_missing")
            observed_bytes = b""
        else:
            observed_bytes = record_path.read_bytes()
            if hashlib.sha256(observed_bytes).hexdigest() != (
                manifest.representative_records.sha256
            ):
                errors.append("representative_records_hash_mismatch")
        if population is not None:
            quality_full_distribution = (
                manifest.selection_version
                == REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION
            )
            expected_max_token_count = (
                REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
                if quality_full_distribution
                else REPRESENTATIVE_MAX_TOKEN_COUNT
            )
            if manifest.selection_version not in {
                REPRESENTATIVE_SELECTION_VERSION,
                REPRESENTATIVE_QUALITY_FULL_SELECTION_VERSION,
            }:
                errors.append("representative_selection_version_unknown")
            if file_sha256(population_path) != manifest.population_manifest_sha256:
                errors.append("representative_population_hash_mismatch")
            identity_pairs = (
                ("run_id", manifest.run_id, population.run_id),
                ("cluster_id", manifest.cluster_id, population.cluster_id),
                ("cutoff_at", manifest.cutoff_at, population.cutoff_at),
                (
                    "memory_snapshot_id",
                    manifest.memory_snapshot_id,
                    population.memory_snapshot_id,
                ),
                (
                    "source_generation_sha256",
                    manifest.source_generation_sha256,
                    population.source_generation_sha256,
                ),
                (
                    "corpus_manifest_sha256",
                    manifest.corpus_manifest_sha256,
                    population.corpus_manifest_sha256,
                ),
            )
            errors.extend(
                f"representative_{name}_population_mismatch"
                for name, observed, expected in identity_pairs
                if observed != expected
            )
            try:
                recomputed = self._select(
                    population,
                    population_path=population_path,
                    query=manifest.query_text,
                    force_database_verification=False,
                    allow_distribution_shortfall=quality_full_distribution,
                )
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"representative_recompute_failed:{exc}")
            else:
                expected_identity = _representative_identity(
                    population,
                    population_path=population_path,
                    query=manifest.query_text,
                    embedding_model=manifest.embedding_model,
                    query_embedding_sha256=recomputed.query_embedding_sha256,
                    selection_version=manifest.selection_version,
                    max_token_count=expected_max_token_count,
                )
                expected_id = "REP-" + sha256_text(canonical_json(expected_identity))[
                    :20
                ].upper()
                if expected_id != manifest.representative_set_id:
                    errors.append("representative_set_id_mismatch")
                expected_bytes = _jsonl_bytes(
                    [row.model_dump(mode="json") for row in recomputed.rows]
                )
                if expected_bytes != observed_bytes:
                    errors.append("representative_records_recomputed_mismatch")
                if manifest.selected_record_ids != [
                    row.record_id for row in recomputed.rows
                ]:
                    errors.append("representative_selected_ids_mismatch")
                expected_strata = _strata_contract(recomputed)
                distribution_error = _max_distribution_share_error(
                    expected_strata,
                    population_unit_count=population.independent_unit_count,
                    selected_unit_count=len(recomputed.rows),
                )
                expected_summary = {
                    "selection_version": manifest.selection_version,
                    "embedding_model": self.memory_index.embedding_provider.embedding_method,
                    "query_embedding_sha256": recomputed.query_embedding_sha256,
                    "candidate_pool_count": recomputed.candidate_pool_count,
                    "target_selected_record_count": (
                        recomputed.target_selected_record_count
                    ),
                    "population_record_count": population.raw_record_count,
                    "population_unit_count": population.independent_unit_count,
                    "selected_record_count": len(recomputed.rows),
                    "selected_unit_count": len(recomputed.rows),
                    "omitted_population_record_count": (
                        population.raw_record_count - len(recomputed.rows)
                    ),
                    "omitted_population_unit_count": (
                        population.independent_unit_count - len(recomputed.rows)
                    ),
                    "max_selected_record_count": REPRESENTATIVE_MAX_SELECTED_RECORDS,
                    "max_candidate_pool_count": REPRESENTATIVE_MAX_CANDIDATE_POOL,
                    "max_token_count": expected_max_token_count,
                    "max_trade_date_concentration": (
                        REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION
                    ),
                    "max_unit_key_concentration": (
                        REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION
                    ),
                    "estimated_token_count": len(expected_bytes),
                    "diversity_coverage_ratio": (
                        sum(1 for item in expected_strata if item.selected_unit_count)
                        / len(expected_strata)
                        if expected_strata
                        else 0.0
                    ),
                    "max_distribution_share_error": distribution_error,
                    "distribution_share_error_tolerance": (
                        REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE
                    ),
                    "strata": [item.model_dump(mode="json") for item in expected_strata],
                    "selected_record_ids": [row.record_id for row in recomputed.rows],
                    "selected_independent_unit_ids": [
                        row.independent_unit_id for row in recomputed.rows
                    ],
                    "representative_records": _artifact_reference(
                        self.root,
                        record_path,
                        expected_bytes,
                    ).model_dump(mode="json"),
                }
                observed_summary = {
                    key: manifest.model_dump(mode="json")[key]
                    for key in expected_summary
                }
                if observed_summary != expected_summary:
                    errors.append("representative_manifest_summary_mismatch")
        return {
            **base,
            "passed": not errors,
            "errors": errors,
            "manifest": manifest.model_dump(mode="json"),
        }

    def _validated_population(
        self,
        path: Path,
        *,
        force_database_verification: bool,
    ) -> PopulationManifest:
        if force_database_verification:
            inspection = self.population_retriever.inspect(
                path,
                force_database_verification=True,
            )
        else:
            try:
                observed = PopulationManifest.model_validate(read_json(path))
            except (OSError, ValueError) as exc:
                raise ValueError(f"population manifest is invalid: {exc}") from exc
            inspection = _inspect_built_population(
                self.root,
                manifest_path=path,
                expected_manifest=observed,
            )
        if inspection["passed"] is not True:
            raise ValueError(
                "population manifest is not current: "
                + ", ".join(inspection["errors"])
            )
        return PopulationManifest.model_validate(read_json(path))

    def _select(
        self,
        population: PopulationManifest,
        *,
        population_path: Path,
        query: str,
        force_database_verification: bool,
        query_vector: list[float] | None = None,
        allow_distribution_shortfall: bool = False,
    ) -> _Selection:
        member_rows = _read_jsonl(
            self.root / population.member_records.artifact_path
        )
        unit_rows = _read_jsonl(
            self.root / population.independent_units.artifact_path
        )
        candidates = _unit_candidates(
            member_rows,
            unit_rows,
            population.cutoff_at,
            query_regime_cluster=population.query_regime_cluster,
        )
        if not candidates:
            raise ValueError("population has no representative candidates")
        population_strata: dict[str, set[str]] = defaultdict(set)
        for candidate in candidates:
            for stratum in candidate.strata:
                population_strata[stratum].add(candidate.independent_unit_id)
        pool = _stratified_candidate_pool(
            candidates,
            population.corpus_manifest_sha256,
        )
        snapshot, source_rows = self.memory_index.representative_source_records(
            [candidate.record_id for candidate in pool],
            cutoff_at=population.cutoff_at,
            force_database_verification=force_database_verification,
        )
        if snapshot.snapshot_id != population.memory_snapshot_id:
            raise ValueError("representative snapshot differs from population snapshot")
        source_by_id = {row.record_id: row for row in source_rows}
        if query_vector is None:
            query_vectors = self.memory_index.embedding_provider.embed_texts([query])
            if len(query_vectors) != 1:
                raise ValueError("embedding provider returned the wrong query vector count")
            query_vector = query_vectors[0]
        query_vector = _normalized_embedding_vector(
            query_vector,
            field="representative query",
        )
        target_selected_record_count = _selection_target_count(candidates)
        rows = _mmr_select(
            pool,
            source_by_id,
            query_vector,
            target_selected_record_count=target_selected_record_count,
            population_strata=population_strata,
            max_token_count=(
                REPRESENTATIVE_QUALITY_FULL_MAX_TOKEN_COUNT
                if allow_distribution_shortfall
                else REPRESENTATIVE_MAX_TOKEN_COUNT
            ),
        )
        return _Selection(
            rows=rows,
            candidate_pool_count=len(pool),
            target_selected_record_count=target_selected_record_count,
            population_strata=dict(population_strata),
            query_embedding_sha256=sha256_text(canonical_json(query_vector)),
        )


def _inspect_built_representative(
    root: Path,
    *,
    manifest_path: Path,
    expected_manifest: RepresentativeSetManifest,
) -> dict[str, Any]:
    """Verify the written representative closure without re-running MMR."""

    errors: list[str] = []
    try:
        observed = RepresentativeSetManifest.model_validate(read_json(manifest_path))
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"representative_manifest_invalid:{exc}"]}
    if observed != expected_manifest:
        errors.append("representative_manifest_serialization_mismatch")
    records_path = (root / observed.representative_records.artifact_path).resolve()
    try:
        records_path.relative_to(manifest_path.parent.resolve())
    except ValueError:
        errors.append("representative_records_path_escape")
    else:
        if not records_path.exists():
            errors.append("representative_records_missing")
        elif file_sha256(records_path) != observed.representative_records.sha256:
            errors.append("representative_records_hash_mismatch")
        else:
            try:
                rows = _read_jsonl(records_path)
            except (OSError, ValueError):
                errors.append("representative_records_invalid")
            else:
                if len(rows) != observed.representative_records.item_count:
                    errors.append("representative_records_count_mismatch")
                if [str(row.get("record_id")) for row in rows] != (
                    observed.selected_record_ids
                ):
                    errors.append("representative_records_identity_mismatch")
    return {"passed": not errors, "errors": errors}


def _load_cached_representative(
    root: Path,
    *,
    manifest_path: Path,
    expected_identity: dict[str, Any],
) -> RepresentativeSetManifest | None:
    """Reuse only a current representative manifest with a closed record artifact."""

    if not manifest_path.is_file():
        return None
    try:
        manifest = RepresentativeSetManifest.model_validate(read_json(manifest_path))
    except (OSError, ValueError) as exc:
        raise ValueError(f"cached representative manifest is invalid: {exc}") from exc
    expected_id = "REP-" + sha256_text(canonical_json(expected_identity))[:20].upper()
    if (
        manifest.representative_set_id != expected_id
        or _representative_identity_from_manifest(manifest) != expected_identity
    ):
        raise ValueError("cached representative identity differs from the active request")
    inspection = _inspect_built_representative(
        root,
        manifest_path=manifest_path,
        expected_manifest=manifest,
    )
    if inspection["passed"] is not True:
        raise ValueError(
            "cached representative closure is invalid: "
            + ", ".join(str(error) for error in inspection["errors"])
        )
    return manifest


def _representative_identity_from_manifest(
    manifest: RepresentativeSetManifest,
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.representative_set_identity.v1",
        "run_id": manifest.run_id,
        "cluster_id": manifest.cluster_id,
        "cutoff_at": manifest.cutoff_at.isoformat(),
        "population_id": manifest.population_id,
        "population_manifest_sha256": manifest.population_manifest_sha256,
        "memory_snapshot_id": manifest.memory_snapshot_id,
        "source_generation_sha256": manifest.source_generation_sha256,
        "query_sha256": manifest.query_sha256,
        "query_embedding_sha256": manifest.query_embedding_sha256,
        "selection_version": manifest.selection_version,
        "embedding_model": manifest.embedding_model,
        "max_selected_record_count": manifest.max_selected_record_count,
        "max_candidate_pool_count": manifest.max_candidate_pool_count,
        "max_token_count": manifest.max_token_count,
        "max_trade_date_concentration": manifest.max_trade_date_concentration,
        "max_unit_key_concentration": manifest.max_unit_key_concentration,
    }


def _unit_candidates(
    member_rows: list[dict[str, Any]],
    unit_rows: list[dict[str, Any]],
    cutoff_at: datetime,
    *,
    query_regime_cluster: str | None = None,
) -> list[_Candidate]:
    members_by_unit: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for member in member_rows:
        members_by_unit[str(member["independent_unit_id"])].append(member)
    candidates: list[_Candidate] = []
    cutoff_date = as_kst(cutoff_at).date()
    for unit in sorted(unit_rows, key=lambda item: str(item["independent_unit_id"])):
        unit_id = str(unit["independent_unit_id"])
        members = members_by_unit.get(unit_id, [])
        if not members:
            raise ValueError("population unit has no member records")
        trade_date = date.fromisoformat(str(unit["trade_date"]))
        unit_candidates: dict[tuple[str, ...], _Candidate] = {}
        for member in sorted(members, key=lambda item: str(item["record_id"])):
            strata = _candidate_strata(
                unit,
                member,
                cutoff_date=cutoff_date,
                query_regime_cluster=query_regime_cluster,
            )
            candidate = _Candidate(
                record_id=str(member["record_id"]),
                independent_unit_id=unit_id,
                trade_date=trade_date,
                concentration_key=_unit_concentration_key(unit_id),
                strata=tuple(sorted(strata)),
                record_label_quality=str(member.get("label_quality", "missing")),
            )
            unit_candidates.setdefault(candidate.strata, candidate)
        candidates.extend(unit_candidates.values())
    return candidates


def _strata_contract(selection: _Selection) -> list[RepresentativeStratum]:
    selected_strata: dict[str, list[RepresentativeRecord]] = defaultdict(list)
    for row in selection.rows:
        for stratum in row.strata:
            selected_strata[stratum].append(row)
    return [
        RepresentativeStratum(
            stratum=stratum,
            population_unit_count=len(unit_ids),
            selected_unit_count=len(
                {row.independent_unit_id for row in selected_strata.get(stratum, [])}
            ),
            record_ids=sorted(
                {row.record_id for row in selected_strata.get(stratum, [])}
            ),
            independent_unit_ids=sorted(
                {row.independent_unit_id for row in selected_strata.get(stratum, [])}
            ),
        )
        for stratum, unit_ids in sorted(selection.population_strata.items())
    ]


def _candidate_strata(
    unit: dict[str, Any],
    member: dict[str, Any],
    *,
    cutoff_date: date,
    query_regime_cluster: str | None,
) -> set[str]:
    trade_date = date.fromisoformat(str(unit["trade_date"]))
    high = unit.get("high_return_pct")
    outcome = (
        "SUCCESS_HIGH10"
        if isinstance(high, (int, float)) and float(high) >= 10.0
        else "POSITIVE_LOW"
        if isinstance(high, (int, float)) and float(high) >= 5.0
        else "NEGATIVE"
        if isinstance(high, (int, float)) and float(high) < 0.0
        else "BOUNDARY"
        if isinstance(high, (int, float))
        else "MISSING"
    )
    age = "RECENT_1Y" if (cutoff_date - trade_date).days <= 365 else "OLDER"
    regime_values = {
        str(value).strip().upper()
        for value in unit.get("regime_clusters") or ["UNKNOWN"]
        if str(value).strip()
    }
    regime_class = (
        "CONFLICTING"
        if "CONFLICTING" in regime_values or len(regime_values) > 1
        else "UNKNOWN"
        if not regime_values or regime_values == {"UNKNOWN"}
        else "SAME"
        if query_regime_cluster
        and regime_values == {query_regime_cluster.strip().upper()}
        else "DIFFERENT"
        if query_regime_cluster
        else "KNOWN"
    )
    strata = {
        f"polarity:{unit.get('polarity', 'UNKNOWN')}",
        f"outcome:{outcome}",
        f"age:{age}",
        f"regime_class:{regime_class}",
        f"unit_quality:{unit.get('label_quality', 'missing')}",
        f"record_quality:{member.get('label_quality', 'missing')}",
    }
    record_type = str(member.get("record_type", "unknown"))
    path = str(member.get("path_type", "UNKNOWN"))
    memory_lanes = {
        str(value).strip()
        for value in member.get("memory_lanes") or []
        if str(value).strip()
    }
    strata.add(f"path_class:{_path_class(path)}")
    strata.update(_record_family_strata({record_type}))
    strata.update(f"lane:{value}" for value in memory_lanes)
    if record_type == "counterexample":
        strata.add("role:COUNTEREXAMPLE")
    if record_type == "newsless_or_unexplained_case":
        strata.add("role:UNEXPLAINED")
    if record_type in CANDIDATE_ERROR_RECORD_TYPES:
        strata.add("role:CANDIDATE_ERROR")
    return strata


def _path_class(value: str) -> str:
    normalized = value.strip().upper()
    if any(marker in normalized for marker in ("THEME", "BENEFICIARY")):
        return "THEME_BENEFICIARY"
    if any(marker in normalized for marker in ("DIRECT", "SINGLE_EVENT")):
        return "DIRECT"
    if any(marker in normalized for marker in ("CONTINUATION", "MARKET_MEMORY")):
        return "CONTINUATION"
    return "OTHER"


def _record_family_strata(record_types: set[str]) -> set[str]:
    families = set()
    for record_type in record_types:
        normalized = record_type.lower()
        family = (
            "BENEFICIARY"
            if "beneficiary" in normalized
            else "THEME"
            if "theme" in normalized or "leader_preference" in normalized
            else "DIRECT"
            if "direct_event" in normalized
            else "ISSUER_DAY"
            if "issuer_day" in normalized
            else "NEWSLESS"
            if "newsless" in normalized
            else "CANDIDATE_ERROR"
            if record_type in CANDIDATE_ERROR_RECORD_TYPES
            else "OTHER"
        )
        families.add(f"family:{family}")
    return families


def _required_strata(candidates: list[_Candidate]) -> set[str]:
    prefixes = (
        "polarity:",
        "role:",
        "record_quality:",
        "path_class:",
        "family:",
        "lane:",
    )
    return {
        stratum
        for candidate in candidates
        for stratum in candidate.strata
        if stratum.startswith(prefixes)
    }


def _selection_target_count(candidates: list[_Candidate]) -> int:
    unit_ids = {candidate.independent_unit_id for candidate in candidates}
    units_by_trade_date: dict[date, set[str]] = defaultdict(set)
    units_by_concentration_key: dict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        units_by_trade_date[candidate.trade_date].add(candidate.independent_unit_id)
        units_by_concentration_key[candidate.concentration_key].add(
            candidate.independent_unit_id
        )
    if not unit_ids:
        raise ValueError("representative candidate set is empty")
    dimension_entropies = []
    for prefix in (
        "polarity:",
        "outcome:",
        "regime_class:",
        "unit_quality:",
        "record_quality:",
        "path_class:",
        "family:",
        "lane:",
    ):
        units_by_value: dict[str, set[str]] = defaultdict(set)
        for candidate in candidates:
            for stratum in candidate.strata:
                if stratum.startswith(prefix):
                    units_by_value[stratum].add(candidate.independent_unit_id)
        counts = [len(values) for values in units_by_value.values() if values]
        total = sum(counts)
        if len(counts) < 2 or not total:
            continue
        entropy = -sum(
            (count / total) * math.log2(count / total) for count in counts
        )
        dimension_entropies.append(entropy / math.log2(len(counts)))
    normalized_entropy = (
        sum(dimension_entropies) / len(dimension_entropies)
        if dimension_entropies
        else 0.0
    )
    adaptive_target = REPRESENTATIVE_INITIAL_MIN_RECORDS + round(
        (
            REPRESENTATIVE_INITIAL_MAX_RECORDS
            - REPRESENTATIVE_INITIAL_MIN_RECORDS
        )
        * normalized_entropy
    )
    feasible_trade_date_count = sum(
        min(len(values), REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION)
        for values in units_by_trade_date.values()
    )
    feasible_concentration_count = sum(
        min(len(values), REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION)
        for values in units_by_concentration_key.values()
    )
    return min(
        len(unit_ids),
        feasible_trade_date_count,
        feasible_concentration_count,
        REPRESENTATIVE_MAX_SELECTED_RECORDS,
        max(1, adaptive_target),
    )


def _distribution_gain(
    candidate: _Candidate,
    *,
    population_shares: dict[str, float],
    selected_strata: Counter[str],
    selected_unit_count: int,
) -> float:
    if not population_shares:
        return 1.0

    def error(*, after: bool) -> float:
        denominator = selected_unit_count + (1 if after else 0)
        if denominator < 1:
            return 1.0
        return sum(
            abs(
                (
                    selected_strata[stratum]
                    + (1 if after and stratum in candidate.strata else 0)
                )
                / denominator
                - population_share
            )
            for stratum, population_share in population_shares.items()
        ) / len(population_shares)

    improvement = error(after=False) - error(after=True)
    return max(0.0, min(1.0, 0.5 + 0.5 * improvement))


def _max_distribution_share_error(
    strata: list[RepresentativeStratum],
    *,
    population_unit_count: int,
    selected_unit_count: int,
) -> float:
    if not strata or population_unit_count < 1 or selected_unit_count < 1:
        return 0.0
    return max(
        abs(
            item.population_unit_count / population_unit_count
            - item.selected_unit_count / selected_unit_count
        )
        for item in strata
    )


def _distribution_error_from_counts(
    population_shares: dict[str, float],
    *,
    selected_strata: Counter[str],
    selected_unit_count: int,
) -> float:
    if not population_shares or selected_unit_count < 1:
        return 1.0
    return max(
        abs(
            population_share
            - selected_strata[stratum] / selected_unit_count
        )
        for stratum, population_share in population_shares.items()
    )


def _most_underrepresented_stratum(
    population_shares: dict[str, float],
    *,
    selected_strata: Counter[str],
    selected_unit_count: int,
) -> str | None:
    if not population_shares:
        return None
    denominator = max(1, selected_unit_count)
    deficits = {
        stratum: (
            population_share
            - selected_strata[stratum] / denominator
        )
        for stratum, population_share in population_shares.items()
    }
    stratum, deficit = max(deficits.items(), key=lambda item: (item[1], item[0]))
    return stratum if deficit > 0.0 else None


def _population_stratum_shares(
    population_strata: dict[str, set[str]],
) -> dict[str, float]:
    population_units = {
        unit_id for unit_ids in population_strata.values() for unit_id in unit_ids
    }
    if not population_units:
        return {}
    return {
        stratum: len(unit_ids) / len(population_units)
        for stratum, unit_ids in population_strata.items()
    }


def _stratified_candidate_pool(
    candidates: list[_Candidate],
    selection_salt: str,
) -> list[_Candidate]:
    groups: dict[str, list[_Candidate]] = defaultdict(list)
    candidates_by_unit: dict[str, list[_Candidate]] = defaultdict(list)
    for candidate in candidates:
        candidates_by_unit[candidate.independent_unit_id].append(candidate)
        for stratum in candidate.strata:
            groups[stratum].append(candidate)
    for stratum, values in groups.items():
        values.sort(
            key=lambda item: sha256_text(
                f"{selection_salt}|{stratum}|{item.independent_unit_id}"
                f"|{item.record_id}"
            )
        )
    selected: dict[str, _Candidate] = {}
    selected_units: set[str] = set()
    for stratum in sorted(groups, key=lambda item: (len(groups[item]), item)):
        if len(selected) >= REPRESENTATIVE_MAX_STRATUM_RESERVE:
            break
        candidate = next(
            (
                item
                for item in groups[stratum]
                if item.independent_unit_id not in selected_units
            ),
            groups[stratum][0],
        )
        selected.setdefault(candidate.record_id, candidate)
        selected_units.add(candidate.independent_unit_id)
        if len(selected) >= REPRESENTATIVE_MAX_CANDIDATE_POOL:
            break
    ordered_units = sorted(
        candidates_by_unit,
        key=lambda unit_id: sha256_text(f"{selection_salt}|unit|{unit_id}"),
    )
    for unit_id in ordered_units:
        if len(selected) >= REPRESENTATIVE_MAX_CANDIDATE_POOL:
            break
        if unit_id in selected_units:
            continue
        candidate = min(
            candidates_by_unit[unit_id],
            key=lambda item: sha256_text(
                f"{selection_salt}|base|{item.independent_unit_id}|{item.record_id}"
            ),
        )
        selected[candidate.record_id] = candidate
        selected_units.add(unit_id)
    for stratum in sorted(groups, key=lambda item: (len(groups[item]), item)):
        if len(selected) >= REPRESENTATIVE_MAX_CANDIDATE_POOL:
            break
        if any(
            stratum in candidate.strata for candidate in selected.values()
        ):
            continue
        candidate = groups[stratum][0]
        selected[candidate.record_id] = candidate
    return sorted(
        selected.values(),
        key=lambda item: (item.independent_unit_id, item.record_id),
    )


def _mmr_select(
    candidates: list[_Candidate],
    source_by_id: dict[str, RepresentativeSourceRecord],
    query_vector: list[float],
    *,
    target_selected_record_count: int,
    population_strata: dict[str, set[str]],
    max_token_count: int = REPRESENTATIVE_MAX_TOKEN_COUNT,
) -> list[RepresentativeRecord]:
    if max_token_count < 1:
        raise ValueError("representative max token count must be positive")
    missing_sources = sorted(
        candidate.record_id
        for candidate in candidates
        if candidate.record_id not in source_by_id
    )
    if missing_sources:
        raise ValueError(
            "representative source records missing: " + ", ".join(missing_sources)
        )
    remaining = {candidate.record_id: candidate for candidate in candidates}
    selected_rows: list[RepresentativeRecord] = []
    selected_candidate_indices: list[int] = []
    selected_units: set[str] = set()
    covered_strata: set[str] = set()
    selected_strata: Counter[str] = Counter()
    trade_dates: Counter[date] = Counter()
    concentration_keys: Counter[str] = Counter()
    token_count = 0
    required_strata = _required_strata(candidates)
    anchor_by_unit: dict[str, _Candidate] = {}
    for candidate in sorted(
        candidates,
        key=lambda item: (
            sha256_text(f"facility|{item.independent_unit_id}"),
            item.record_id,
        ),
    ):
        anchor_by_unit.setdefault(candidate.independent_unit_id, candidate)
    anchors = list(anchor_by_unit.values())[:REPRESENTATIVE_FACILITY_ANCHOR_COUNT]
    candidate_index_by_id = {
        candidate.record_id: index for index, candidate in enumerate(candidates)
    }
    similarity_matrix = _pairwise_normalized_cosines(
        [source_by_id[candidate.record_id].embedding for candidate in candidates]
    )
    relevance_by_id = {
        candidate.record_id: _normalized_cosine(
            query_vector,
            source_by_id[candidate.record_id].embedding,
        )
        for candidate in candidates
    }
    facility_similarities = {
        candidate.record_id: tuple(
            float(
                similarity_matrix[
                    candidate_index_by_id[candidate.record_id],
                    candidate_index_by_id[anchor.record_id],
                ]
            )
            for anchor in anchors
        )
        for candidate in candidates
    }
    facility_coverage = [0.0] * len(anchors)
    population_shares = _population_stratum_shares(population_strata)
    while remaining and len(selected_rows) < REPRESENTATIVE_MAX_SELECTED_RECORDS:
        uncovered_required = required_strata - covered_strata
        distribution_error = _distribution_error_from_counts(
            population_shares,
            selected_strata=selected_strata,
            selected_unit_count=len(selected_rows),
        )
        if (
            len(selected_rows) >= target_selected_record_count
            and not uncovered_required
            and distribution_error
            <= REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE
        ):
            break
        target_stratum = (
            min(
                uncovered_required,
                key=lambda stratum: (
                    sum(stratum in item.strata for item in candidates),
                    stratum,
                ),
            )
            if uncovered_required
            else _most_underrepresented_stratum(
                population_shares,
                selected_strata=selected_strata,
                selected_unit_count=len(selected_rows),
            )
            if distribution_error
            > REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE
            else None
        )
        best: tuple[float, str, _Candidate, RepresentativeRecord] | None = None
        for candidate in remaining.values():
            if candidate.independent_unit_id in selected_units:
                continue
            if target_stratum is not None and target_stratum not in candidate.strata:
                continue
            if (
                trade_dates[candidate.trade_date]
                >= REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION
            ):
                continue
            if (
                concentration_keys[candidate.concentration_key]
                >= REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION
            ):
                continue
            source = source_by_id[candidate.record_id]
            candidate_index = candidate_index_by_id[candidate.record_id]
            relevance = relevance_by_id[candidate.record_id]
            diversity = (
                1.0
                if not selected_candidate_indices
                else min(
                    1.0 - float(similarity_matrix[candidate_index, selected_index])
                    for selected_index in selected_candidate_indices
                )
            )
            coverage = len(set(candidate.strata) - covered_strata) / len(
                candidate.strata
            )
            similarities = facility_similarities[candidate.record_id]
            facility = (
                sum(
                    max(0.0, similarity - facility_coverage[index])
                    for index, similarity in enumerate(similarities)
                )
                / len(similarities)
                if similarities
                else 0.0
            )
            distribution = _distribution_gain(
                candidate,
                population_shares=population_shares,
                selected_strata=selected_strata,
                selected_unit_count=len(selected_rows),
            )
            score = (
                0.35 * relevance
                + 0.20 * diversity
                + 0.15 * coverage
                + 0.15 * facility
                + 0.15 * distribution
            )
            if best is None or (score, candidate.record_id) > best[:2]:
                row = _representative_row(
                    candidate,
                    source,
                    rank=len(selected_rows) + 1,
                    relevance=relevance,
                    diversity=diversity,
                    facility=facility,
                    distribution=distribution,
                    selection_score=score,
                    max_estimated_tokens=max(
                        512,
                        max_token_count // max(target_selected_record_count, 1) - 1,
                    ),
                )
                if (
                    token_count + row.estimated_token_count + 1
                    <= max_token_count
                ):
                    best = (score, candidate.record_id, candidate, row)
        if best is None:
            break
        _score, _record_id, candidate, row = best
        source = source_by_id[candidate.record_id]
        selected_rows.append(row)
        selected_candidate_indices.append(candidate_index_by_id[candidate.record_id])
        selected_units.add(candidate.independent_unit_id)
        covered_strata.update(candidate.strata)
        selected_strata.update(candidate.strata)
        for index, similarity in enumerate(facility_similarities[candidate.record_id]):
            facility_coverage[index] = max(facility_coverage[index], similarity)
        trade_dates[candidate.trade_date] += 1
        concentration_keys[candidate.concentration_key] += 1
        token_count += row.estimated_token_count + 1
        for record_id, remaining_candidate in tuple(remaining.items()):
            if remaining_candidate.independent_unit_id == candidate.independent_unit_id:
                remaining.pop(record_id)
    if not selected_rows:
        raise RepresentativeSelectionBudgetError(
            "representative selection cannot satisfy its budgets"
        )
    uncovered_required = required_strata - covered_strata
    if uncovered_required:
        raise RepresentativeSelectionBudgetError(
            "representative selection cannot cover required strata within budgets: "
            + ", ".join(sorted(uncovered_required))
        )
    if len(selected_rows) < target_selected_record_count:
        raise RepresentativeSelectionBudgetError(
            "representative selection cannot reach its adaptive target"
        )
    if (
        _distribution_error_from_counts(
            population_shares,
            selected_strata=selected_strata,
            selected_unit_count=len(selected_rows),
        )
        > REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE
    ):
        raise RepresentativeSelectionBudgetError(
            "representative selection cannot preserve population distribution "
            "within its budget"
        )
    return selected_rows


def _representative_row(
    candidate: _Candidate,
    source: RepresentativeSourceRecord,
    *,
    rank: int,
    relevance: float,
    diversity: float,
    facility: float,
    distribution: float,
    selection_score: float,
    max_estimated_tokens: int,
) -> RepresentativeRecord:
    base = {
        "rank": rank,
        "record_id": candidate.record_id,
        "independent_unit_id": candidate.independent_unit_id,
        "trade_date": candidate.trade_date.isoformat(),
        "source_sha256": source.source_sha256,
        "provenance_source_ids": list(source.provenance_source_ids),
        "record_label_quality": candidate.record_label_quality,
        "strata": list(candidate.strata),
        "relevance_score": relevance,
        "diversity_score": diversity,
        "facility_score": facility,
        "distribution_score": distribution,
        "selection_score": selection_score,
    }
    excerpt_limit = min(len(source.document), REPRESENTATIVE_CONTEXT_EXCERPT_CHARS)
    for _truncation in range(12):
        estimated_tokens = 1
        row: RepresentativeRecord | None = None
        for _iteration in range(8):
            row = RepresentativeRecord(
                **base,
                context_excerpt=source.document[:excerpt_limit].strip(),
                estimated_token_count=estimated_tokens,
            )
            observed = conservative_token_upper_bound(
                canonical_json(row.model_dump(mode="json"))
            )
            if observed == estimated_tokens:
                break
            estimated_tokens = observed
        else:
            raise ValueError("representative token estimate did not converge")
        if row is not None and row.estimated_token_count <= max_estimated_tokens:
            return row
        if excerpt_limit == 0:
            break
        excerpt_limit = max(
            0,
            min(
                excerpt_limit - 1,
                math.floor(
                    excerpt_limit
                    * max_estimated_tokens
                    / max(estimated_tokens, 1)
                )
                - 8,
            ),
        )
    raise RepresentativeSelectionBudgetError(
        "representative metadata exceeds its per-row token budget"
    )


def _normalized_cosine(left: list[float] | tuple[float, ...], right: tuple[float, ...]) -> float:
    _require_finite_vector(left, field="left representative")
    _require_finite_vector(right, field="right representative")
    if len(left) != len(right):
        raise ValueError("representative embedding dimensions conflict")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    cosine = sum(a * b for a, b in zip(left, right, strict=True)) / (
        left_norm * right_norm
    )
    return max(0.0, min(1.0, (cosine + 1.0) / 2.0))


def _pairwise_normalized_cosines(
    vectors: list[tuple[float, ...]],
) -> npt.NDArray[np.float64]:
    """Compute the reusable MMR similarity matrix in one bounded BLAS operation."""

    if not vectors:
        return np.empty((0, 0), dtype=np.float64)
    matrix = np.asarray(vectors, dtype=np.float64)
    if matrix.ndim != 2 or matrix.shape[1] < 1 or not np.isfinite(matrix).all():
        raise ValueError("representative similarity vectors must be finite and rectangular")
    norms = np.linalg.norm(matrix, axis=1)
    nonzero = norms > 0.0
    normalized = np.zeros_like(matrix)
    normalized[nonzero] = matrix[nonzero] / norms[nonzero, np.newaxis]
    similarities = np.clip((normalized @ normalized.T + 1.0) / 2.0, 0.0, 1.0)
    similarities[~nonzero, :] = 0.0
    similarities[:, ~nonzero] = 0.0
    return similarities


def _require_finite_vector(
    vector: list[float] | tuple[float, ...],
    *,
    field: str,
) -> None:
    if not vector or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        for value in vector
    ):
        raise ValueError(f"{field} embedding must be non-empty and finite")


def _normalized_embedding_vector(
    vector: list[float],
    *,
    field: str,
) -> list[float]:
    _require_finite_vector(vector, field=field)
    return [struct.unpack("!f", struct.pack("!f", value))[0] for value in vector]


def _representative_identity(
    population: PopulationManifest,
    *,
    population_path: Path,
    query: str,
    embedding_model: str,
    query_embedding_sha256: str,
    selection_version: str = REPRESENTATIVE_SELECTION_VERSION,
    max_token_count: int = REPRESENTATIVE_MAX_TOKEN_COUNT,
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.representative_set_identity.v1",
        "run_id": population.run_id,
        "cluster_id": population.cluster_id,
        "cutoff_at": population.cutoff_at.isoformat(),
        "population_id": population.population_id,
        "population_manifest_sha256": file_sha256(population_path),
        "memory_snapshot_id": population.memory_snapshot_id,
        "source_generation_sha256": population.source_generation_sha256,
        "query_sha256": sha256_text(query),
        "query_embedding_sha256": query_embedding_sha256,
        "selection_version": selection_version,
        "embedding_model": embedding_model,
        "max_selected_record_count": REPRESENTATIVE_MAX_SELECTED_RECORDS,
        "max_candidate_pool_count": REPRESENTATIVE_MAX_CANDIDATE_POOL,
        "max_token_count": max_token_count,
        "max_trade_date_concentration": REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION,
        "max_unit_key_concentration": REPRESENTATIVE_MAX_UNIT_KEY_CONCENTRATION,
    }


def _unit_concentration_key(independent_unit_id: str) -> str:
    parts = independent_unit_id.split(":")
    prefix = parts[0]
    if prefix in {
        "EVENT_ISSUER_DAY",
        "ISSUER_DAY",
        "THEME_DAY_TICKER_DAY",
        "TICKER_DAY",
    }:
        return f"ISSUER:{parts[-1]}"
    if prefix in {"THEME_DAY", "THEME_DAY_PAIR"} and len(parts) >= 3:
        return f"THEME:{parts[2]}"
    return independent_unit_id


def _population_manifest_path(
    root: Path,
    manifest: RepresentativeSetManifest,
) -> Path:
    return (
        root
        / "runs"
        / "populations"
        / _safe_segment(manifest.run_id, field="run_id")
        / _safe_segment(manifest.cluster_id, field="cluster_id")
        / _safe_segment(manifest.population_id, field="population_id")
        / "population_manifest.json"
    ).resolve()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("representative source JSONL rows must be objects")
        rows.append(value)
    return rows


def _jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join((canonical_json(row) + "\n").encode("utf-8") for row in rows)


def _artifact_reference(root: Path, path: Path, payload: bytes) -> ArtifactReference:
    return ArtifactReference(
        artifact_path=relative_to_root(path, root),
        sha256=hashlib.sha256(payload).hexdigest(),
        item_count=payload.count(b"\n"),
    )


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError(f"immutable representative artifact conflict: {path.name}")
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def _write_immutable_manifest(path: Path, manifest: RepresentativeSetManifest) -> None:
    if path.exists():
        existing = RepresentativeSetManifest.model_validate(read_json(path))
        if existing != manifest:
            raise ValueError("immutable representative manifest conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    write_json(temporary, manifest.model_dump(mode="json"))
    os.replace(temporary, path)


def _safe_segment(value: str, *, field: str) -> str:
    stripped = value.strip()
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
    if (
        not stripped
        or value != stripped
        or stripped in {".", ".."}
        or any(character not in allowed for character in stripped)
    ):
        raise ValueError(f"{field} contains unsafe path characters")
    return stripped


def _require_under(path: Path, root: Path) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("representative artifact path escapes its root") from exc
