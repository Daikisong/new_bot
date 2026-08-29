"""Immutable ANN index for cutoff-safe compiled brain claims."""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    CategoryBrainIndexManifest,
    CategoryBrainQueryPlan,
    CategoryClaimInclusionProof,
    CategoryClaimMerkleStep,
)
from news_scalping_lab.records.models import CompiledBrainClaim
from news_scalping_lab.retrieval.embedding import LocalEmbeddingProvider
from news_scalping_lab.utils import (
    as_kst,
    canonical_json,
    file_sha256,
    read_json,
    relative_to_root,
    sha256_text,
    write_json,
)

CATEGORY_BRAIN_INDEX_VERSION = "category_brain_claim_ann.v1"
CATEGORY_BRAIN_INDEX_ROOT = Path("runs/checkpoints/category_brain_index")
CATEGORY_BRAIN_INDEX_MANIFEST_FILE = "category_brain_index_manifest.json"
CATEGORY_BRAIN_VECTOR_LEDGER_FILE = "claim_vectors.jsonl"
CATEGORY_BRAIN_DATABASE_FILE = "claim_index.duckdb"
CATEGORY_BRAIN_EMBEDDING_BATCH_SIZE = 128
_FILE_HASH_CACHE: dict[Path, tuple[int, int, int, str]] = {}


class CategoryBrainIndex:
    def __init__(
        self,
        root: Path,
        manifest_path: Path,
        *,
        embedding_provider: LocalEmbeddingProvider | None = None,
    ) -> None:
        self.root = root.resolve()
        self.manifest_path = manifest_path.resolve()
        self.manifest = CategoryBrainIndexManifest.model_validate(
            read_json(self.manifest_path)
        )
        self.embedding_provider = embedding_provider
        database_path = self.root / self.manifest.database_artifact_path
        _require_file_hash(database_path, self.manifest.database_sha256)
        self.connection = duckdb.connect(str(database_path), read_only=True)
        self.connection.execute("LOAD vss")

    def close(self) -> None:
        self.connection.close()

    def query(
        self,
        *,
        cluster_id: str,
        query: str,
        limit: int = 3,
        query_vector: list[float] | None = None,
    ) -> CategoryBrainQueryPlan:
        provider = self.embedding_provider
        if provider is None:
            raise ValueError("category brain query requires an embedding provider")
        if provider.embedding_method != self.manifest.embedding_model:
            raise ValueError("category brain query embedding model mismatch")
        if query_vector is None:
            query_vectors = provider.embed_texts([query])
            if len(query_vectors) != 1:
                raise ValueError("category brain query embedding count mismatch")
            query_vector = query_vectors[0]
        normalized_query_vector = _float32_vector(query_vector)
        if len(normalized_query_vector) != self.manifest.embedding_dimensions:
            raise ValueError("category brain query embedding dimension mismatch")
        vector_type = f"FLOAT[{self.manifest.embedding_dimensions}]"
        rows = self.connection.execute(
            f"""
            SELECT claim_id,
                   embedding,
                   1.0 - array_cosine_distance(embedding, ?::{vector_type}) AS score,
                   claim_json
            FROM claims
            ORDER BY array_cosine_distance(embedding, ?::{vector_type}), claim_id
            LIMIT ?
            """,
            [
                normalized_query_vector,
                normalized_query_vector,
                min(max(1, limit), 3),
            ],
        ).fetchall()
        selected_claims = [
            CompiledBrainClaim.model_validate(json.loads(str(row[3]))) for row in rows
        ]
        expanded_query = expanded_category_query(query, selected_claims)
        return CategoryBrainQueryPlan(
            cluster_id=cluster_id,
            original_query=query,
            original_query_sha256=sha256_text(query),
            query_embedding_sha256=sha256_text(canonical_json(normalized_query_vector)),
            embedding_model=self.manifest.embedding_model,
            selected_claim_ids=[str(row[0]) for row in rows],
            claim_embedding_sha256s={
                str(row[0]): sha256_text(
                    canonical_json([float(value) for value in row[1]])
                )
                for row in rows
            },
            selection_scores={str(row[0]): float(row[2]) for row in rows},
            expanded_query=expanded_query,
            expanded_query_sha256=sha256_text(expanded_query),
            source_artifact_path=relative_to_root(
                self.manifest_path,
                self.root,
            ),
            source_artifact_sha256=file_sha256(self.manifest_path),
        )

    def guidance_claims(
        self,
        *,
        selected_record_ids: set[str],
        limit: int,
    ) -> list[CompiledBrainClaim]:
        if not selected_record_ids:
            return []
        placeholders = ", ".join("?" for _value in selected_record_ids)
        rows = self.connection.execute(
            f"""
            SELECT DISTINCT c.claim_id, c.claim_json
            FROM claim_records cr
            JOIN claims c USING (claim_id)
            WHERE cr.record_id IN ({placeholders})
            ORDER BY c.claim_id
            LIMIT ?
            """,
            [*sorted(selected_record_ids), max(1, limit)],
        ).fetchall()
        return [
            CompiledBrainClaim.model_validate(json.loads(str(row[1]))) for row in rows
        ]

    def claims_by_ids(self, claim_ids: set[str]) -> list[CompiledBrainClaim]:
        if not claim_ids:
            return []
        placeholders = ", ".join("?" for _value in claim_ids)
        rows = self.connection.execute(
            f"SELECT claim_id, claim_json FROM claims WHERE claim_id IN ({placeholders}) "
            "ORDER BY claim_id",
            sorted(claim_ids),
        ).fetchall()
        if {str(row[0]) for row in rows} != claim_ids:
            raise ValueError("category brain index selected claim is missing")
        return [
            CompiledBrainClaim.model_validate(json.loads(str(row[1]))) for row in rows
        ]

    def claim_proofs_by_ids(
        self,
        claim_ids: set[str],
    ) -> dict[str, CategoryClaimInclusionProof]:
        if not claim_ids:
            return {}
        placeholders = ", ".join("?" for _value in claim_ids)
        rows = self.connection.execute(
            f"SELECT claim_id, claim_proof_json FROM claims "
            f"WHERE claim_id IN ({placeholders}) ORDER BY claim_id",
            sorted(claim_ids),
        ).fetchall()
        if {str(row[0]) for row in rows} != claim_ids:
            raise ValueError("category brain index claim proof is missing")
        proofs = {
            str(row[0]): CategoryClaimInclusionProof.model_validate(
                json.loads(str(row[1]))
            )
            for row in rows
        }
        if any(
            not verify_category_claim_inclusion_proof(
                proof,
                self.manifest.claim_payload_merkle_root_sha256,
            )
            for proof in proofs.values()
        ):
            raise ValueError("category brain index claim proof is invalid")
        return proofs


def build_category_brain_index(
    root: Path,
    *,
    brain_version: str,
    brain_record_cutoff_at: datetime,
    claims: list[CompiledBrainClaim],
    embedding_provider: LocalEmbeddingProvider,
) -> tuple[CategoryBrainIndexManifest, Path]:
    root = root.resolve()
    cutoff_at = as_kst(brain_record_cutoff_at)
    if any(as_kst(claim.available_from) > cutoff_at for claim in claims):
        raise ValueError("category brain index contains a claim after the brain cutoff")
    output_dir = root / CATEGORY_BRAIN_INDEX_ROOT / brain_version
    manifest_path = output_dir / CATEGORY_BRAIN_INDEX_MANIFEST_FILE
    if manifest_path.exists():
        manifest = CategoryBrainIndexManifest.model_validate(read_json(manifest_path))
        inspection = inspect_category_brain_index(
            root,
            manifest_path,
            claims_override=claims,
        )
        if (
            inspection["passed"] is not True
            or manifest.embedding_model != embedding_provider.embedding_method
            or manifest.index_version != CATEGORY_BRAIN_INDEX_VERSION
            or manifest.brain_version != brain_version
            or as_kst(manifest.brain_record_cutoff_at) != cutoff_at
        ):
            raise ValueError("existing category brain index is invalid")
        return manifest, manifest_path
    if not claims:
        raise ValueError("category brain index requires compiled claims")
    claim_ids = [claim.claim_id for claim in claims]
    if len(claim_ids) != len(set(claim_ids)):
        raise ValueError("category brain index claim identifiers must be unique")
    documents = [category_claim_document(claim) for claim in claims]
    vectors: list[list[float]] = []
    for offset in range(0, len(documents), CATEGORY_BRAIN_EMBEDDING_BATCH_SIZE):
        vectors.extend(
            embedding_provider.embed_texts(
                documents[offset : offset + CATEGORY_BRAIN_EMBEDDING_BATCH_SIZE]
            )
        )
    if len(vectors) != len(claims):
        raise ValueError("category brain index embedding count mismatch")
    normalized_vectors = [_float32_vector(vector) for vector in vectors]
    dimensions = len(normalized_vectors[0])
    if dimensions < 1 or any(len(vector) != dimensions for vector in normalized_vectors):
        raise ValueError("category brain index embedding dimensions mismatch")
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{brain_version}-", dir=parent))
    try:
        ledger_path = staging / CATEGORY_BRAIN_VECTOR_LEDGER_FILE
        ledger_rows = [
            _ledger_row(claim, document=document, vector=vector)
            for claim, document, vector in zip(
                claims,
                documents,
                normalized_vectors,
                strict=True,
            )
        ]
        claim_proofs, claim_merkle_root = _claim_inclusion_proofs(ledger_rows)
        ledger_path.write_bytes(
            "".join(canonical_json(row) + "\n" for row in ledger_rows).encode("utf-8")
        )
        database_path = staging / CATEGORY_BRAIN_DATABASE_FILE
        _write_category_database(
            database_path,
            ledger_rows=ledger_rows,
            claim_proofs=claim_proofs,
            dimensions=dimensions,
        )
        final_ledger_path = output_dir / CATEGORY_BRAIN_VECTOR_LEDGER_FILE
        final_database_path = output_dir / CATEGORY_BRAIN_DATABASE_FILE
        snapshot_claims_path = (
            root / "brain" / "snapshots" / brain_version / "compiled_claims.jsonl"
        )
        claims_text = "".join(claim.model_dump_json() + "\n" for claim in claims)
        manifest = CategoryBrainIndexManifest(
            brain_version=brain_version,
            brain_record_cutoff_at=cutoff_at,
            index_version=CATEGORY_BRAIN_INDEX_VERSION,
            embedding_model=embedding_provider.embedding_method,
            embedding_dimensions=dimensions,
            claim_count=len(claims),
            claim_payload_merkle_root_sha256=claim_merkle_root,
            claims_artifact=ArtifactReference(
                artifact_path=relative_to_root(snapshot_claims_path, root),
                sha256=sha256_text(claims_text),
                item_count=len(claims),
            ),
            vector_ledger=ArtifactReference(
                artifact_path=relative_to_root(final_ledger_path, root),
                sha256=file_sha256(ledger_path),
                item_count=len(ledger_rows),
            ),
            database_artifact_path=relative_to_root(final_database_path, root),
            database_sha256=file_sha256(database_path),
            hnsw_index_ready=True,
        )
        write_json(
            staging / CATEGORY_BRAIN_INDEX_MANIFEST_FILE,
            manifest.model_dump(mode="json"),
        )
        os.replace(staging, output_dir)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return manifest, manifest_path


def query_category_brain_index(
    root: Path,
    manifest_path: Path,
    *,
    cluster_id: str,
    query: str,
    embedding_provider: LocalEmbeddingProvider,
    limit: int = 3,
) -> CategoryBrainQueryPlan:
    index = CategoryBrainIndex(
        root,
        manifest_path,
        embedding_provider=embedding_provider,
    )
    try:
        return index.query(cluster_id=cluster_id, query=query, limit=limit)
    finally:
        index.close()


def category_guidance_claims(
    root: Path,
    manifest_path: Path,
    *,
    selected_record_ids: set[str],
    limit: int,
) -> list[CompiledBrainClaim]:
    index = CategoryBrainIndex(root, manifest_path)
    try:
        return index.guidance_claims(
            selected_record_ids=selected_record_ids,
            limit=limit,
        )
    finally:
        index.close()


def category_claims_by_ids(
    root: Path,
    manifest_path: Path,
    *,
    claim_ids: set[str],
) -> list[CompiledBrainClaim]:
    index = CategoryBrainIndex(root, manifest_path)
    try:
        return index.claims_by_ids(claim_ids)
    finally:
        index.close()


def category_claim_proofs_by_ids(
    root: Path,
    manifest_path: Path,
    *,
    claim_ids: set[str],
) -> dict[str, CategoryClaimInclusionProof]:
    index = CategoryBrainIndex(root, manifest_path)
    try:
        return index.claim_proofs_by_ids(claim_ids)
    finally:
        index.close()


def inspect_category_brain_index(
    root: Path,
    manifest_path: Path,
    *,
    claims_override: list[CompiledBrainClaim] | None = None,
    deep: bool = True,
) -> dict[str, Any]:
    root = root.resolve()
    path = manifest_path.resolve()
    errors: list[str] = []
    try:
        manifest = CategoryBrainIndexManifest.model_validate(read_json(path))
    except (OSError, ValueError) as exc:
        return {"passed": False, "errors": [f"category_brain_index_invalid:{exc}"]}
    expected_path = (
        root
        / CATEGORY_BRAIN_INDEX_ROOT
        / manifest.brain_version
        / CATEGORY_BRAIN_INDEX_MANIFEST_FILE
    ).resolve()
    if path != expected_path:
        errors.append("category_brain_index_manifest_path_mismatch")
    claims_path = (root / manifest.claims_artifact.artifact_path).resolve()
    ledger_path = (root / manifest.vector_ledger.artifact_path).resolve()
    database_path = (root / manifest.database_artifact_path).resolve()
    for label, artifact_path in (
        ("claims", claims_path),
        ("vector_ledger", ledger_path),
        ("database", database_path),
    ):
        try:
            artifact_path.relative_to(root)
        except ValueError:
            errors.append(f"category_brain_index_{label}_path_escape")
    if claims_override is None and not _file_hash_matches(
        claims_path,
        manifest.claims_artifact.sha256,
        force=deep,
    ):
        errors.append("category_brain_index_claims_hash_mismatch")
    if not _file_hash_matches(
        ledger_path,
        manifest.vector_ledger.sha256,
        force=deep,
    ):
        errors.append("category_brain_index_vector_ledger_hash_mismatch")
    if not _file_hash_matches(
        database_path,
        manifest.database_sha256,
        force=deep,
    ):
        errors.append("category_brain_index_database_hash_mismatch")
    if not deep:
        return {
            "passed": not errors,
            "errors": sorted(set(errors)),
            "manifest": manifest.model_dump(mode="json"),
            "claim_ids": [],
        }
    if claims_override is not None:
        claims = claims_override
        claims_text = "".join(claim.model_dump_json() + "\n" for claim in claims)
        if sha256_text(claims_text) != manifest.claims_artifact.sha256:
            errors.append("category_brain_index_claims_hash_mismatch")
    elif not claims_path.is_file():
        errors.append("category_brain_index_claims_hash_mismatch")
        claims = []
    else:
        try:
            claims = [CompiledBrainClaim.model_validate(row) for row in _read_jsonl(claims_path)]
        except (OSError, ValueError):
            claims = []
            errors.append("category_brain_index_claims_invalid")
    if not ledger_path.is_file():
        errors.append("category_brain_index_vector_ledger_hash_mismatch")
        ledger_rows: list[dict[str, Any]] = []
    else:
        try:
            ledger_rows = _read_jsonl(ledger_path)
        except (OSError, ValueError):
            ledger_rows = []
            errors.append("category_brain_index_vector_ledger_invalid")
    if (
        len(claims) != manifest.claim_count
        or len(ledger_rows) != manifest.claim_count
        or manifest.claims_artifact.item_count != len(claims)
        or manifest.vector_ledger.item_count != len(ledger_rows)
    ):
        errors.append("category_brain_index_claim_count_mismatch")
    if any(
        as_kst(claim.available_from) > as_kst(manifest.brain_record_cutoff_at)
        for claim in claims
    ):
        errors.append("category_brain_index_claim_after_cutoff")
    expected_ledger = {
        claim.claim_id: claim for claim in claims
    }
    ledger_by_id = {
        str(row.get("claim_id")): row for row in ledger_rows if isinstance(row, dict)
    }
    if set(expected_ledger) != set(ledger_by_id):
        errors.append("category_brain_index_claim_identity_mismatch")
    for claim_id, claim in expected_ledger.items():
        row = ledger_by_id.get(claim_id)
        if row is None or not _ledger_row_matches_claim(row, claim):
            errors.append("category_brain_index_claim_projection_mismatch")
            break
    if not database_path.is_file():
        errors.append("category_brain_index_database_hash_mismatch")
    elif ledger_rows:
        try:
            connection = duckdb.connect(str(database_path), read_only=True)
            database_rows = connection.execute(
                "SELECT claim_id, claim_sha256, document_sha256, embedding, claim_json, "
                "claim_proof_json "
                "FROM claims ORDER BY claim_id"
            ).fetchall()
            index_names = {
                str(row[0])
                for row in connection.execute(
                    "SELECT index_name FROM duckdb_indexes()"
                ).fetchall()
            }
        except duckdb.Error:
            errors.append("category_brain_index_database_invalid")
        else:
            expected_proofs, expected_merkle_root = _claim_inclusion_proofs(ledger_rows)
            if expected_merkle_root != manifest.claim_payload_merkle_root_sha256:
                errors.append("category_brain_index_claim_merkle_root_mismatch")
            expected_rows = [
                (
                    str(row["claim_id"]),
                    str(row["claim_sha256"]),
                    str(row["document_sha256"]),
                    [float(value) for value in row["embedding"]],
                    canonical_json(row["claim"]),
                    canonical_json(
                        expected_proofs[str(row["claim_id"])].model_dump(mode="json")
                    ),
                )
                for row in sorted(ledger_rows, key=lambda item: str(item["claim_id"]))
            ]
            observed_rows = [
                (
                    str(row[0]),
                    str(row[1]),
                    str(row[2]),
                    [float(value) for value in row[3]],
                    str(row[4]),
                    str(row[5]),
                )
                for row in database_rows
            ]
            if observed_rows != expected_rows:
                errors.append("category_brain_index_database_projection_mismatch")
            if "category_claims_hnsw_idx" not in index_names:
                errors.append("category_brain_index_hnsw_missing")
        finally:
            if "connection" in locals():
                connection.close()
    return {
        "passed": not errors,
        "errors": sorted(set(errors)),
        "manifest": manifest.model_dump(mode="json"),
        "claim_ids": sorted(expected_ledger),
    }


def category_claim_document(claim: CompiledBrainClaim) -> str:
    return "\n".join(
        value.strip()
        for value in (
            claim.category,
            claim.statement,
            claim.mechanism,
            claim.scope,
            *claim.conditions,
            *claim.boundary_conditions,
            *claim.failure_modes,
        )
        if value.strip()
    )


def expanded_category_query(query: str, claims: list[CompiledBrainClaim]) -> str:
    if not claims:
        return query
    guidance = " ; ".join(
        f"{claim.category}: {claim.mechanism}" for claim in claims
    )
    return f"{query} | category brain query guidance: {guidance}"


def _ledger_row(
    claim: CompiledBrainClaim,
    *,
    document: str,
    vector: list[float],
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.category_brain_claim_vector.v1",
        "claim_id": claim.claim_id,
        "claim_sha256": sha256_text(canonical_json(claim.model_dump(mode="json"))),
        "claim": claim.model_dump(mode="json"),
        "document_sha256": sha256_text(document),
        "embedding_sha256": sha256_text(canonical_json(vector)),
        "embedding": vector,
    }


def _ledger_row_matches_claim(row: dict[str, Any], claim: CompiledBrainClaim) -> bool:
    vector = row.get("embedding")
    if not isinstance(vector, list):
        return False
    try:
        normalized = _float32_vector(vector)
    except ValueError:
        return False
    return row == _ledger_row(
        claim,
        document=category_claim_document(claim),
        vector=normalized,
    )


def _write_category_database(
    path: Path,
    *,
    ledger_rows: list[dict[str, Any]],
    claim_proofs: dict[str, CategoryClaimInclusionProof],
    dimensions: int,
) -> None:
    connection = duckdb.connect(str(path))
    try:
        connection.execute("INSTALL vss")
        connection.execute("LOAD vss")
        connection.execute("SET hnsw_enable_experimental_persistence = true")
        connection.execute(
            f"""
            CREATE TABLE claims(
                claim_id VARCHAR PRIMARY KEY,
                claim_sha256 VARCHAR NOT NULL,
                document_sha256 VARCHAR NOT NULL,
                embedding FLOAT[{dimensions}] NOT NULL,
                claim_json VARCHAR NOT NULL,
                claim_proof_json VARCHAR NOT NULL
            )
            """
        )
        connection.executemany(
            "INSERT INTO claims VALUES (?, ?, ?, ?, ?, ?)",
            [
                (
                    row["claim_id"],
                    row["claim_sha256"],
                    row["document_sha256"],
                    row["embedding"],
                    canonical_json(row["claim"]),
                    canonical_json(
                        claim_proofs[str(row["claim_id"])].model_dump(mode="json")
                    ),
                )
                for row in ledger_rows
            ],
        )
        connection.execute(
            "CREATE INDEX category_claims_hnsw_idx ON claims USING HNSW (embedding) "
            "WITH (metric = 'cosine')"
        )
        connection.execute(
            "CREATE TABLE claim_records(record_id VARCHAR NOT NULL, claim_id VARCHAR NOT NULL, "
            "role VARCHAR NOT NULL)"
        )
        claim_record_rows: list[tuple[str, str, str]] = []
        for row in ledger_rows:
            claim = CompiledBrainClaim.model_validate(row["claim"])
            claim_record_rows.extend(
                (record_id, claim.claim_id, "supporting")
                for record_id in claim.supporting_record_ids
            )
            claim_record_rows.extend(
                (record_id, claim.claim_id, "contradicting")
                for record_id in claim.contradicting_record_ids
            )
        if claim_record_rows:
            connection.executemany(
                "INSERT INTO claim_records VALUES (?, ?, ?)",
                claim_record_rows,
            )
        connection.execute("CREATE INDEX claim_records_record_idx ON claim_records(record_id)")
        connection.execute("CHECKPOINT")
    finally:
        connection.close()


def claim_payload_sha256(claim: CompiledBrainClaim) -> str:
    return sha256_text(canonical_json(claim.model_dump(mode="json")))


def verify_category_claim_inclusion_proof(
    proof: CategoryClaimInclusionProof,
    merkle_root_sha256: str,
) -> bool:
    current = _claim_merkle_leaf(proof.claim_id, proof.claim_payload_sha256)
    index = proof.leaf_index
    count = proof.leaf_count
    if count < 1 or index >= count:
        return False
    for step in proof.siblings:
        sibling_index = index ^ 1
        if sibling_index >= count:
            sibling_index = index
        expected_position = "LEFT" if sibling_index < index else "RIGHT"
        if step.position != expected_position:
            return False
        current = (
            sha256_text(step.sha256 + current)
            if step.position == "LEFT"
            else sha256_text(current + step.sha256)
        )
        index //= 2
        count = (count + 1) // 2
    return count == 1 and current == merkle_root_sha256


def _claim_inclusion_proofs(
    ledger_rows: list[dict[str, Any]],
) -> tuple[dict[str, CategoryClaimInclusionProof], str]:
    ordered = sorted(
        (
            str(row["claim_id"]),
            str(row["claim_sha256"]),
        )
        for row in ledger_rows
    )
    if not ordered:
        raise ValueError("category brain claim Merkle tree is empty")
    leaves = [_claim_merkle_leaf(claim_id, digest) for claim_id, digest in ordered]
    levels = [leaves]
    while len(levels[-1]) > 1:
        level = levels[-1]
        levels.append(
            [
                sha256_text(level[offset] + level[min(offset + 1, len(level) - 1)])
                for offset in range(0, len(level), 2)
            ]
        )
    proofs: dict[str, CategoryClaimInclusionProof] = {}
    for leaf_index, (claim_id, digest) in enumerate(ordered):
        siblings: list[CategoryClaimMerkleStep] = []
        index = leaf_index
        for level in levels[:-1]:
            sibling_index = index ^ 1
            if sibling_index >= len(level):
                sibling_index = index
            siblings.append(
                CategoryClaimMerkleStep(
                    position="LEFT" if sibling_index < index else "RIGHT",
                    sha256=level[sibling_index],
                )
            )
            index //= 2
        proofs[claim_id] = CategoryClaimInclusionProof(
            claim_id=claim_id,
            claim_payload_sha256=digest,
            leaf_index=leaf_index,
            leaf_count=len(ordered),
            siblings=siblings,
        )
    return proofs, levels[-1][0]


def _claim_merkle_leaf(claim_id: str, claim_payload_digest: str) -> str:
    return sha256_text(
        canonical_json(
            {
                "claim_id": claim_id,
                "claim_payload_sha256": claim_payload_digest,
            }
        )
    )


def _merkle_root(leaves: list[str]) -> str:
    level = leaves
    while len(level) > 1:
        level = [
            sha256_text(level[offset] + level[min(offset + 1, len(level) - 1)])
            for offset in range(0, len(level), 2)
        ]
    return level[0]


def _float32_vector(values: list[Any]) -> list[float]:
    if not values:
        raise ValueError("category brain embedding is empty")
    result: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("category brain embedding is invalid")
        numeric = float(value)
        if not math.isfinite(numeric):
            raise ValueError("category brain embedding is non-finite")
        result.append(struct.unpack("<f", struct.pack("<f", numeric))[0])
    return result


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("category brain index rows must be objects")
        rows.append(row)
    return rows


def _require_file_hash(path: Path, expected_sha256: str) -> None:
    if not _file_hash_matches(path, expected_sha256, force=False):
        raise ValueError(f"category brain artifact hash mismatch: {path}")


def _file_hash_matches(path: Path, expected_sha256: str, *, force: bool) -> bool:
    if not path.is_file():
        return False
    stat = path.stat()
    identity = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    cached = _FILE_HASH_CACHE.get(path.resolve())
    if not force and cached is not None and cached[:3] == identity:
        return cached[3] == expected_sha256
    observed = file_sha256(path)
    _FILE_HASH_CACHE[path.resolve()] = (*identity, observed)
    return observed == expected_sha256
