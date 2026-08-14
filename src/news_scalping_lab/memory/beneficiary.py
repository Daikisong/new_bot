"""Cutoff-safe beneficiary path projection for open-world candidates."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.contracts.memory_context import (
    AdaptiveTriggerEvidence,
    ArtifactReference,
    BeneficiaryGraphArtifact,
    BeneficiaryGraphPath,
    EventClusterManifest,
)
from news_scalping_lab.contracts.models import Candidate, CompanyMemory
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    stable_id,
    write_json,
)

BENEFICIARY_GRAPH_VERSION = "beneficiary_graph_projection.v1"
BENEFICIARY_GRAPH_ROOT = Path("runs/checkpoints/beneficiary_graph")
BENEFICIARY_GRAPH_INPUT_ROOT = Path("runs/checkpoints/beneficiary_graph_inputs")
BENEFICIARY_TRIGGER_QUERY_TERM_LIMIT = 12


def build_beneficiary_graph(
    root: Path,
    *,
    run_id: str,
    cutoff_at: datetime,
    event_cluster_manifest_path: Path,
    candidates: list[Candidate],
    company_memory_context: list[dict[str, Any]],
) -> tuple[BeneficiaryGraphArtifact, Path]:
    root = root.resolve()
    cluster_path = event_cluster_manifest_path.resolve()
    cluster_manifest = EventClusterManifest.model_validate(read_json(cluster_path))
    if cluster_manifest.run_id != run_id:
        raise ValueError("beneficiary graph run differs from event clusters")
    if as_kst(cluster_manifest.cutoff_at) != as_kst(cutoff_at):
        raise ValueError("beneficiary graph cutoff differs from event clusters")
    candidate_keys = {
        value.strip().casefold()
        for candidate in candidates
        for value in (candidate.ticker, candidate.company_name)
        if value.strip()
    }
    (
        company_hashes,
        excluded_company_paths,
        company_memories,
        reviewed_company_count,
        reviewed_company_root,
        unmatched_company_count,
    ) = (
        _company_memory_context_projection(
            root,
            company_memory_context,
            cutoff_at=cutoff_at,
            candidate_keys=candidate_keys,
        )
    )
    candidate_payload = [item.model_dump(mode="json") for item in candidates]
    candidate_input_sha256 = sha256_text(canonical_json(candidate_payload))
    candidate_input_path = (
        root
        / BENEFICIARY_GRAPH_INPUT_ROOT
        / run_id
        / f"CANDIDATES-{candidate_input_sha256[:20].upper()}"
        / "candidates.jsonl"
    )
    _write_immutable_bytes(
        candidate_input_path,
        "".join(canonical_json(item) + "\n" for item in candidate_payload).encode("utf-8"),
    )
    paths, unresolved = _project_graph_paths(
        run_id=run_id,
        cutoff_at=cutoff_at,
        cluster_manifest=cluster_manifest,
        candidates=candidates,
        company_memories=company_memories,
    )
    artifact = BeneficiaryGraphArtifact(
        run_id=run_id,
        cutoff_at=cutoff_at,
        event_cluster_manifest=ArtifactReference(
            artifact_path=relative_to_root(cluster_path, root),
            sha256=file_sha256(cluster_path),
            item_count=1,
        ),
        company_memory_artifact_sha256s=company_hashes,
        excluded_company_memory_artifact_paths=sorted(set(excluded_company_paths)),
        reviewed_company_memory_count=reviewed_company_count,
        reviewed_company_memory_root_sha256=reviewed_company_root,
        unmatched_company_memory_count=unmatched_company_count,
        candidate_input_artifact=ArtifactReference(
            artifact_path=relative_to_root(candidate_input_path, root),
            sha256=file_sha256(candidate_input_path),
            item_count=len(candidate_payload),
        ),
        candidate_input_sha256=candidate_input_sha256,
        candidate_count=len(candidate_payload),
        path_count=len(paths),
        paths=paths,
        unresolved_candidate_ids=sorted(unresolved),
    )
    graph_id = "BG-" + sha256_text(
        canonical_json(artifact.model_dump(mode="json"))
    )[:20].upper()
    path = (
        root
        / BENEFICIARY_GRAPH_ROOT
        / run_id
        / graph_id
        / "beneficiary_graph.json"
    )
    _write_immutable(path, artifact)
    return artifact, path


def inspect_beneficiary_graph(root: Path, path: Path) -> dict[str, Any]:
    root = root.resolve()
    resolved = path.resolve()
    errors: list[str] = []
    try:
        artifact = BeneficiaryGraphArtifact.model_validate(read_json(resolved))
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"beneficiary_graph_invalid:{exc}"]}
    graph_id = "BG-" + sha256_text(
        canonical_json(artifact.model_dump(mode="json"))
    )[:20].upper()
    expected = (
        root
        / BENEFICIARY_GRAPH_ROOT
        / artifact.run_id
        / graph_id
        / "beneficiary_graph.json"
    ).resolve()
    if resolved != expected:
        errors.append("beneficiary_graph_path_mismatch")
    cluster_path = _resolve_artifact_reference(
        root,
        artifact.event_cluster_manifest,
        label="event_cluster_manifest",
        errors=errors,
    )
    candidate_path = _resolve_artifact_reference(
        root,
        artifact.candidate_input_artifact,
        label="candidate_input",
        errors=errors,
    )
    cluster_manifest: EventClusterManifest | None = None
    candidates: list[Candidate] = []
    if cluster_path is not None:
        try:
            cluster_manifest = EventClusterManifest.model_validate(read_json(cluster_path))
        except (OSError, ValueError):
            errors.append("beneficiary_graph_event_cluster_manifest_invalid")
        else:
            if (
                cluster_manifest.run_id != artifact.run_id
                or as_kst(cluster_manifest.cutoff_at) != as_kst(artifact.cutoff_at)
            ):
                errors.append("beneficiary_graph_event_cluster_identity_mismatch")
    if candidate_path is not None:
        try:
            candidate_rows = _read_jsonl(candidate_path)
            candidates = [Candidate.model_validate(row) for row in candidate_rows]
        except (OSError, ValueError):
            errors.append("beneficiary_graph_candidate_input_invalid")
        else:
            candidate_payload = [item.model_dump(mode="json") for item in candidates]
            if (
                len(candidates) != artifact.candidate_count
                or sha256_text(canonical_json(candidate_payload))
                != artifact.candidate_input_sha256
            ):
                errors.append("beneficiary_graph_candidate_input_mismatch")
    company_memories = _company_memories_from_artifact(
        root,
        artifact,
        errors=errors,
    )
    reviewed_company_hashes = dict(
        sorted(artifact.company_memory_artifact_sha256s.items())
    )
    if (
        len(reviewed_company_hashes) != artifact.reviewed_company_memory_count
        or sha256_text(canonical_json(reviewed_company_hashes))
        != artifact.reviewed_company_memory_root_sha256
    ):
        errors.append("beneficiary_graph_company_memory_review_root_mismatch")
    if (
        artifact.unmatched_company_memory_count != 0
    ):
        errors.append("beneficiary_graph_unmatched_company_memory_count_mismatch")
    if (
        cluster_manifest is not None
        and len(candidates) == artifact.candidate_count
        and not beneficiary_graph_projection_matches(
            artifact,
            cluster_manifest=cluster_manifest,
            candidates=candidates,
            company_memories=company_memories,
        )
    ):
        errors.append("beneficiary_graph_sources_recomputed_mismatch")
    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "artifact": artifact.model_dump(mode="json"),
    }


def beneficiary_graph_projection_matches(
    artifact: BeneficiaryGraphArtifact,
    *,
    cluster_manifest: EventClusterManifest,
    candidates: list[Candidate],
    company_memories: list[tuple[str, CompanyMemory]],
) -> bool:
    if (
        cluster_manifest.run_id != artifact.run_id
        or as_kst(cluster_manifest.cutoff_at) != as_kst(artifact.cutoff_at)
        or len(candidates) != artifact.candidate_count
        or sha256_text(
            canonical_json([item.model_dump(mode="json") for item in candidates])
        )
        != artifact.candidate_input_sha256
    ):
        return False
    company_paths = [path for path, _memory in company_memories]
    if (
        len(company_paths) != len(set(company_paths))
        or set(company_paths) != set(artifact.company_memory_artifact_sha256s)
        or artifact.reviewed_company_memory_count != len(company_paths)
        or artifact.reviewed_company_memory_root_sha256
        != sha256_text(
            canonical_json(dict(sorted(artifact.company_memory_artifact_sha256s.items())))
        )
        or artifact.unmatched_company_memory_count != 0
        or any(
            as_kst(memory.known_at) > as_kst(artifact.cutoff_at)
            or as_kst(memory.available_from) > as_kst(artifact.cutoff_at)
            for _path, memory in company_memories
        )
    ):
        return False
    expected_paths, expected_unresolved = _project_graph_paths(
        run_id=artifact.run_id,
        cutoff_at=artifact.cutoff_at,
        cluster_manifest=cluster_manifest,
        candidates=candidates,
        company_memories=sorted(company_memories, key=lambda item: item[0]),
    )
    return (
        artifact.path_count == len(expected_paths)
        and artifact.paths == expected_paths
        and artifact.unresolved_candidate_ids == expected_unresolved
    )


def beneficiary_trigger_evidence(
    root: Path,
    graph_path: Path,
    *,
    cluster_id: str,
) -> AdaptiveTriggerEvidence | None:
    inspection = inspect_beneficiary_graph(root, graph_path)
    if inspection.get("passed") is not True:
        raise ValueError("beneficiary trigger source graph is invalid")
    graph = BeneficiaryGraphArtifact.model_validate(read_json(graph_path))
    return beneficiary_trigger_evidence_from_artifact(
        graph,
        source_artifact=ArtifactReference(
            artifact_path=relative_to_root(graph_path.resolve(), root.resolve()),
            sha256=file_sha256(graph_path),
            item_count=1,
        ),
        cluster_id=cluster_id,
    )


def beneficiary_trigger_evidence_from_artifact(
    graph: BeneficiaryGraphArtifact,
    *,
    source_artifact: ArtifactReference,
    cluster_id: str,
) -> AdaptiveTriggerEvidence | None:
    paths = [
        item
        for item in graph.paths
        if cluster_id in item.event_cluster_ids
        and item.candidate_path_type == "THEME_BENEFICIARY"
        and len(item.mechanism_steps) >= 2
    ]
    if not paths:
        return None
    source_ids = sorted({source_id for item in paths for source_id in item.source_ids})
    query_terms = _nonblank_unique(
        [
            value
            for item in paths
            for value in (*item.mechanism_steps, *item.business_roles)
        ]
    )[:BENEFICIARY_TRIGGER_QUERY_TERM_LIMIT]
    if not source_ids or not query_terms:
        return None
    return AdaptiveTriggerEvidence(
        kind="MULTI_HOP_BENEFICIARY",
        source_artifact=source_artifact,
        cutoff_at=graph.cutoff_at,
        event_cluster_ids=[cluster_id],
        source_ids=source_ids,
        query_terms=query_terms,
    )


def _company_memory_context_projection(
    root: Path,
    rows: list[dict[str, Any]],
    *,
    cutoff_at: datetime,
    candidate_keys: set[str],
) -> tuple[
    dict[str, str],
    list[str],
    list[tuple[str, CompanyMemory]],
    int,
    str,
    int,
]:
    included_hashes: dict[str, str] = {}
    reviewed_hashes: dict[str, str] = {}
    excluded_paths: list[str] = []
    memories: list[tuple[str, CompanyMemory]] = []
    for row in rows:
        relative_path = row.get("path")
        digest = row.get("sha256")
        supplied_memory = row.get("memory")
        if not isinstance(relative_path, str) or not relative_path.strip():
            continue
        if not isinstance(digest, str) or not isinstance(supplied_memory, dict):
            excluded_paths.append(relative_path)
            continue
        memory_path = (root / relative_path).resolve()
        try:
            memory_path.relative_to(root)
        except ValueError:
            excluded_paths.append(relative_path)
            continue
        try:
            parsed_memory = CompanyMemory.model_validate(read_json(memory_path))
        except (OSError, ValueError):
            excluded_paths.append(relative_path)
            continue
        if (
            not memory_path.is_file()
            or file_sha256(memory_path) != digest
            or parsed_memory.model_dump(
                mode="json",
                exclude={"production_attestation"},
            )
            != supplied_memory
            or as_kst(parsed_memory.known_at) > as_kst(cutoff_at)
            or as_kst(parsed_memory.available_from) > as_kst(cutoff_at)
        ):
            excluded_paths.append(relative_path)
            continue
        if candidate_keys.intersection(_company_memory_keys(parsed_memory)):
            reviewed_hashes[relative_path] = digest
            included_hashes[relative_path] = digest
            memories.append((relative_path, parsed_memory))
    return (
        included_hashes,
        sorted(set(excluded_paths)),
        sorted(memories, key=lambda item: item[0]),
        len(reviewed_hashes),
        sha256_text(canonical_json(dict(sorted(reviewed_hashes.items())))),
        len(reviewed_hashes) - len(included_hashes),
    )


def _company_memories_from_artifact(
    root: Path,
    artifact: BeneficiaryGraphArtifact,
    *,
    errors: list[str],
) -> list[tuple[str, CompanyMemory]]:
    memories: list[tuple[str, CompanyMemory]] = []
    for relative_path, digest in sorted(artifact.company_memory_artifact_sha256s.items()):
        memory_path = (root / relative_path).resolve()
        try:
            memory_path.relative_to(root)
        except ValueError:
            errors.append("beneficiary_graph_company_memory_path_escape")
            continue
        if not memory_path.is_file() or file_sha256(memory_path) != digest:
            errors.append("beneficiary_graph_company_memory_hash_mismatch")
            continue
        try:
            memory = CompanyMemory.model_validate(read_json(memory_path))
        except (OSError, ValueError):
            errors.append("beneficiary_graph_company_memory_invalid")
            continue
        if (
            as_kst(memory.known_at) > as_kst(artifact.cutoff_at)
            or as_kst(memory.available_from) > as_kst(artifact.cutoff_at)
        ):
            errors.append("beneficiary_graph_company_memory_after_cutoff")
            continue
        memories.append((relative_path, memory))
    for relative_path in artifact.excluded_company_memory_artifact_paths:
        excluded_path = (root / relative_path).resolve()
        try:
            excluded_path.relative_to(root)
        except ValueError:
            errors.append("beneficiary_graph_excluded_company_memory_path_escape")
    return memories


def _project_graph_paths(
    *,
    run_id: str,
    cutoff_at: datetime,
    cluster_manifest: EventClusterManifest,
    candidates: list[Candidate],
    company_memories: list[tuple[str, CompanyMemory]],
) -> tuple[list[BeneficiaryGraphPath], list[str]]:
    event_to_cluster: dict[str, tuple[str, tuple[str, ...]]] = {}
    for cluster in cluster_manifest.clusters:
        if cluster.disposition != "MATERIAL_FULL_RETRIEVAL":
            continue
        for event_id in cluster.member_event_ids:
            event_to_cluster[event_id] = (
                cluster.cluster_id,
                tuple(cluster.member_source_ids),
            )
    paths: list[BeneficiaryGraphPath] = []
    unresolved: list[str] = []
    for candidate in sorted(candidates, key=lambda item: item.rank):
        cluster_ids: set[str] = set()
        event_source_ids: set[str] = set()
        for event_id in candidate.event_ids:
            matched_cluster = event_to_cluster.get(event_id)
            if matched_cluster is None:
                continue
            cluster_ids.add(matched_cluster[0])
            event_source_ids.update(matched_cluster[1])
        mechanism_steps = _nonblank_unique(candidate.causal_chain)
        if not cluster_ids or not event_source_ids or not mechanism_steps:
            unresolved.append(_candidate_identity(candidate))
            continue
        candidate_keys = {
            value.strip().casefold()
            for value in (candidate.ticker, candidate.company_name)
            if value.strip()
        }
        matched_memories = [
            (relative_path, memory)
            for relative_path, memory in company_memories
            if candidate_keys.intersection(_company_memory_keys(memory))
        ]
        company_paths = [relative_path for relative_path, _memory in matched_memories]
        business_roles = _nonblank_unique(
            [
                role
                for _relative_path, memory in matched_memories
                for role in memory.supply_chain_roles
            ]
        )
        source_ids = sorted(
            event_source_ids | _cutoff_safe_candidate_source_ids(candidate, cutoff_at=cutoff_at)
        )
        narrative_context = _nonblank_unique([candidate.thesis, candidate.why_now])
        path_id = stable_id(
            "BGP",
            run_id,
            str(candidate.rank),
            candidate.ticker,
            candidate.company_name,
            str(candidate.path_type),
            *sorted(cluster_ids),
            *mechanism_steps,
            *narrative_context,
            *business_roles,
            *company_paths,
            *source_ids,
        )
        paths.append(
            BeneficiaryGraphPath(
                path_id=path_id,
                event_cluster_ids=sorted(cluster_ids),
                mechanism_steps=mechanism_steps,
                narrative_context=narrative_context,
                business_roles=business_roles,
                company_memory_artifact_paths=company_paths,
                ticker=candidate.ticker,
                company_name=candidate.company_name,
                source_ids=source_ids,
                candidate_rank=candidate.rank,
                candidate_path_type=str(candidate.path_type),
            )
        )
    return paths, sorted(unresolved)


def _company_memory_keys(memory: CompanyMemory) -> set[str]:
    return {
        value.strip().casefold()
        for value in (memory.ticker, memory.company_name, *memory.aliases)
        if value.strip()
    }


def _cutoff_safe_candidate_source_ids(
    candidate: Candidate,
    *,
    cutoff_at: datetime,
) -> set[str]:
    result: set[str] = set()
    for provenance in candidate.provenance:
        if (
            provenance.uri.startswith(("candidate://", "prompt://"))
            or provenance.source_type.endswith("_candidate")
            or not provenance.source_id.strip()
        ):
            continue
        if provenance.observed_at is not None and as_kst(provenance.observed_at) > as_kst(cutoff_at):
            continue
        result.add(provenance.source_id)
    return result


def _resolve_artifact_reference(
    root: Path,
    reference: ArtifactReference,
    *,
    label: str,
    errors: list[str],
) -> Path | None:
    path = (root / reference.artifact_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        errors.append(f"beneficiary_graph_{label}_path_escape")
        return None
    if not path.is_file():
        errors.append(f"beneficiary_graph_{label}_missing")
        return None
    if file_sha256(path) != reference.sha256:
        errors.append(f"beneficiary_graph_{label}_hash_mismatch")
        return None
    return path


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("beneficiary graph JSONL rows must be objects")
        rows.append(row)
    return rows


def _candidate_identity(candidate: Candidate) -> str:
    return stable_id("CAND", str(candidate.rank), candidate.ticker, candidate.company_name)


def _nonblank_unique(values: list[Any]) -> list[str]:
    result = []
    for value in values:
        if isinstance(value, str) and value.strip() and value.strip() not in result:
            result.append(value.strip())
    return result


def _write_immutable(path: Path, artifact: BeneficiaryGraphArtifact) -> None:
    payload = artifact.model_dump(mode="json")
    if path.exists():
        if read_json(path) != payload:
            raise ValueError("immutable beneficiary graph conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    write_json(temporary, payload)
    os.replace(temporary, path)


def _write_immutable_bytes(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise ValueError("immutable beneficiary graph input conflict")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
