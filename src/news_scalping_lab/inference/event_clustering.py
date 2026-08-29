"""Cutoff-safe semantic clustering for a complete daily news batch."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from news_scalping_lab.contracts.memory_context import NewsDisposition
from news_scalping_lab.contracts.models import NewsItem
from news_scalping_lab.llm.base import EmbeddingProvider
from news_scalping_lab.policies import EmbeddingFallbackPolicy
from news_scalping_lab.retrieval.embedding import DeterministicHashEmbeddingProvider
from news_scalping_lab.retrieval.production_embedding import (
    ProductionEmbeddingUnavailableError,
    embedding_identity,
)
from news_scalping_lab.utils import canonical_json, sha256_text, stable_id

EVENT_CLUSTERING_VERSION = "semantic_complete_link_v2"
_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<unit>%|억원|조원|만원|원|억|조|만|달러)?"
)
_AFFIRMING_ACTION_PATTERN = re.compile(
    r"(?:체결|확정|승인|성사|개시|수주|signed|approved|confirmed|awarded)",
    re.IGNORECASE,
)
_REVERSING_ACTION_PATTERN = re.compile(
    r"(?:해지|취소|철회|중단|파기|무산|부인|반려|기각|철회|실패|"
    r"terminated|cancelled|canceled|withdrawn|denied|rejected|failed)",
    re.IGNORECASE,
)
_EDITORIAL_PREFIX_PATTERN = re.compile(
    r"^(?:(?:속보|단독|종합|정정|업데이트|breaking|exclusive|update)\s*[:\-]?\s+)+",
    re.IGNORECASE,
)
_KOREAN_COUNTERPARTY_PATTERN = re.compile(
    r"(?:^|[\s,;:])(?P<name>[A-Za-z0-9가-힣&()._-]{1,40})"
    r"(?:에게|에|\s+(?:대상|향))(?=[\s,.]|$)"
)
_ENGLISH_COUNTERPARTY_PATTERN = re.compile(
    r"\b(?:to|for)\s+(?P<name>[A-Z][A-Za-z0-9&()._-]{1,39})\b"
)


@dataclass(frozen=True)
class EventCluster:
    cluster_id: str
    disposition: NewsDisposition
    representative: NewsItem
    members: tuple[NewsItem, ...]
    exact_group_count: int
    exact_duplicate_count: int
    semantic_duplicate_count: int
    minimum_semantic_similarity: float | None
    cluster_signature_sha256: str

    @property
    def material(self) -> bool:
        return self.disposition == "MATERIAL_FULL_RETRIEVAL"


@dataclass(frozen=True)
class EventClusteringResult:
    schema_version: str
    clustering_version: str
    embedding_method: str
    embedding_status: str
    embedding_provider: str
    embedding_model: str | None
    embedding_revision: str | None
    embedding_artifact_sha256: str | None
    embedding_dimensions: int
    embedding_fallback_policy: str
    deterministic_fallback_used: bool
    embedding_retry_count: int
    embedding_failure_type: str | None
    production_runtime_identity: str
    input_row_count: int
    cutoff_safe_row_count: int
    audit_only_row_count: int
    exact_duplicate_count: int
    semantic_duplicate_count: int
    clusters: tuple[EventCluster, ...]
    warnings: tuple[str, ...]

    @property
    def material_clusters(self) -> tuple[EventCluster, ...]:
        return tuple(cluster for cluster in self.clusters if cluster.material)


@dataclass(frozen=True)
class OpenWorldClusterInput:
    cluster_id: str
    representative_text: str
    member_news: tuple[str, ...]
    event_ids: tuple[str, ...]
    row_numbers: tuple[int, ...]


@dataclass(frozen=True)
class _ExactGroup:
    fingerprint: str
    representative: NewsItem
    members: tuple[NewsItem, ...]
    text: str
    number_signature: frozenset[str]
    leading_anchor: tuple[str, ...] | None
    predicate_terms: frozenset[str]
    action_states: frozenset[str]
    counterparties: frozenset[str]


async def cluster_news_events(
    items: Sequence[NewsItem],
    *,
    window_start_at: datetime,
    cutoff_at: datetime,
    embedding_provider: EmbeddingProvider,
    embedding_batch_size: int,
    similarity_threshold: float,
    max_semantic_variants: int = 32,
    fallback_policy: EmbeddingFallbackPolicy | str = (
        EmbeddingFallbackPolicy.ALLOW_DETERMINISTIC_FALLBACK
    ),
    max_retries: int = 0,
    production_runtime_identity: str | None = None,
) -> EventClusteringResult:
    if embedding_batch_size < 1:
        raise ValueError("embedding_batch_size must be positive")
    if not 0.0 <= similarity_threshold <= 1.0:
        raise ValueError("similarity_threshold must be between zero and one")
    if max_semantic_variants < 1:
        raise ValueError("max_semantic_variants must be positive")
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    normalized_fallback_policy = EmbeddingFallbackPolicy.parse(fallback_policy)

    ordered = sorted(items, key=lambda item: item.row_number)
    cutoff_safe = [item for item in ordered if window_start_at <= item.published_at <= cutoff_at]
    audit_only = [
        item
        for item in ordered
        if not window_start_at <= item.published_at <= cutoff_at
    ]
    exact_groups = _exact_groups(cutoff_safe)
    embedding_method = _embedding_provider_identity(embedding_provider)
    provider_identity = embedding_identity(embedding_provider)
    configured_runtime_identity = (
        production_runtime_identity or embedding_method
    )
    observed_method = provider_identity.get("embedding_method")
    if (
        production_runtime_identity is not None
        and isinstance(observed_method, str)
        and observed_method != production_runtime_identity
    ):
        raise ProductionEmbeddingUnavailableError(
            "embedding provider runtime identity drift"
        )
    embedding_status = "PROVIDER"
    deterministic_fallback_used = False
    retry_count = 0
    failure_type: str | None = None
    warnings: list[str] = []
    while True:
        try:
            vectors = await _embed_in_batches(
                embedding_provider,
                [group.text for group in exact_groups],
                batch_size=embedding_batch_size,
            )
            _validate_vectors(vectors, expected_count=len(exact_groups))
            break
        except Exception as exc:
            failure_type = type(exc).__name__
            if retry_count < max_retries:
                retry_count += 1
                continue
            if (
                normalized_fallback_policy
                is EmbeddingFallbackPolicy.FAIL_CLOSED
            ):
                raise ProductionEmbeddingUnavailableError(
                    "production event clustering embedding failed closed: "
                    f"{failure_type}"
                ) from exc
            fallback = DeterministicHashEmbeddingProvider()
            vectors = fallback.embed_texts(
                [group.text for group in exact_groups]
            )
            embedding_method = fallback.embedding_method
            embedding_status = "DETERMINISTIC_FALLBACK"
            deterministic_fallback_used = True
            warnings.append(f"semantic_embedding_fallback:{failure_type}")
            break

    semantic_groups = _complete_link_clusters(
        exact_groups,
        vectors,
        similarity_threshold=similarity_threshold,
        max_semantic_variants=max_semantic_variants,
    )
    clusters = [_material_cluster(groups, exact_groups=exact_groups, vectors=vectors) for groups in semantic_groups]
    clusters.extend(_audit_cluster(item) for item in audit_only)
    clusters.sort(key=lambda cluster: cluster.representative.row_number)
    return EventClusteringResult(
        schema_version="nslab.event_clustering_result.v1",
        clustering_version=EVENT_CLUSTERING_VERSION,
        embedding_method=embedding_method,
        embedding_status=embedding_status,
        embedding_provider=str(
            provider_identity.get("embedding_provider") or type(embedding_provider).__name__
        ),
        embedding_model=_optional_string(provider_identity.get("embedding_model")),
        embedding_revision=_optional_string(
            provider_identity.get("embedding_revision")
        ),
        embedding_artifact_sha256=_optional_string(
            provider_identity.get("embedding_artifact_sha256")
        ),
        embedding_dimensions=(len(vectors[0]) if vectors else 0),
        embedding_fallback_policy=normalized_fallback_policy.value,
        deterministic_fallback_used=deterministic_fallback_used,
        embedding_retry_count=retry_count,
        embedding_failure_type=failure_type,
        production_runtime_identity=configured_runtime_identity,
        input_row_count=len(ordered),
        cutoff_safe_row_count=len(cutoff_safe),
        audit_only_row_count=len(audit_only),
        exact_duplicate_count=sum(cluster.exact_duplicate_count for cluster in clusters),
        semantic_duplicate_count=sum(cluster.semantic_duplicate_count for cluster in clusters),
        clusters=tuple(clusters),
        warnings=tuple(warnings),
    )


def open_world_cluster_inputs(
    result: EventClusteringResult,
) -> list[OpenWorldClusterInput]:
    return [
        OpenWorldClusterInput(
            cluster_id=cluster.cluster_id,
            representative_text=cluster.representative.combined_text,
            member_news=tuple(
                {
                    _exact_fingerprint(item): item.combined_text
                    for item in reversed(cluster.members)
                }.values()
            )[::-1],
            event_ids=tuple(item.event_id for item in cluster.members),
            row_numbers=tuple(item.row_number for item in cluster.members),
        )
        for cluster in result.material_clusters
    ]


def event_clustering_payload(result: EventClusteringResult) -> dict[str, Any]:
    """Serialize a complete clustering result for cross-variant reuse."""

    return {
        "schema_version": result.schema_version,
        "clustering_version": result.clustering_version,
        "embedding_method": result.embedding_method,
        "embedding_status": result.embedding_status,
        "embedding_provider": result.embedding_provider,
        "embedding_model": result.embedding_model,
        "embedding_revision": result.embedding_revision,
        "embedding_artifact_sha256": result.embedding_artifact_sha256,
        "embedding_dimensions": result.embedding_dimensions,
        "embedding_fallback_policy": result.embedding_fallback_policy,
        "deterministic_fallback_used": result.deterministic_fallback_used,
        "embedding_retry_count": result.embedding_retry_count,
        "embedding_failure_type": result.embedding_failure_type,
        "production_runtime_identity": result.production_runtime_identity,
        "input_row_count": result.input_row_count,
        "cutoff_safe_row_count": result.cutoff_safe_row_count,
        "audit_only_row_count": result.audit_only_row_count,
        "exact_duplicate_count": result.exact_duplicate_count,
        "semantic_duplicate_count": result.semantic_duplicate_count,
        "warnings": list(result.warnings),
        "clusters": [
            {
                "cluster_id": cluster.cluster_id,
                "disposition": cluster.disposition,
                "representative_event_id": cluster.representative.event_id,
                "members": [
                    item.model_dump(mode="json") for item in cluster.members
                ],
                "exact_group_count": cluster.exact_group_count,
                "exact_duplicate_count": cluster.exact_duplicate_count,
                "semantic_duplicate_count": cluster.semantic_duplicate_count,
                "minimum_semantic_similarity": (
                    cluster.minimum_semantic_similarity
                ),
                "cluster_signature_sha256": cluster.cluster_signature_sha256,
            }
            for cluster in result.clusters
        ],
    }


def event_clustering_from_payload(payload: object) -> EventClusteringResult:
    """Restore a clustering result without re-embedding the news batch."""

    if not isinstance(payload, dict) or not isinstance(payload.get("clusters"), list):
        raise ValueError("shared event clustering payload is invalid")
    clusters: list[EventCluster] = []
    for raw_cluster in payload["clusters"]:
        if not isinstance(raw_cluster, dict) or not isinstance(
            raw_cluster.get("members"),
            list,
        ):
            raise ValueError("shared event cluster row is invalid")
        members = tuple(
            NewsItem.model_validate(item) for item in raw_cluster["members"]
        )
        representative_event_id = raw_cluster.get("representative_event_id")
        representative = next(
            (
                item
                for item in members
                if item.event_id == representative_event_id
            ),
            None,
        )
        if representative is None:
            raise ValueError("shared event cluster representative is missing")
        disposition = raw_cluster.get("disposition")
        if disposition not in {"MATERIAL_FULL_RETRIEVAL", "AUDIT_ONLY"}:
            raise ValueError("shared event cluster disposition is invalid")
        clusters.append(
            EventCluster(
                cluster_id=_required_string(raw_cluster, "cluster_id"),
                disposition=cast(NewsDisposition, disposition),
                representative=representative,
                members=members,
                exact_group_count=_required_int(raw_cluster, "exact_group_count"),
                exact_duplicate_count=_required_int(
                    raw_cluster,
                    "exact_duplicate_count",
                ),
                semantic_duplicate_count=_required_int(
                    raw_cluster,
                    "semantic_duplicate_count",
                ),
                minimum_semantic_similarity=_optional_float(
                    raw_cluster.get("minimum_semantic_similarity")
                ),
                cluster_signature_sha256=_required_string(
                    raw_cluster,
                    "cluster_signature_sha256",
                ),
            )
        )
    result = EventClusteringResult(
        schema_version=_required_string(payload, "schema_version"),
        clustering_version=_required_string(payload, "clustering_version"),
        embedding_method=_required_string(payload, "embedding_method"),
        embedding_status=_required_string(payload, "embedding_status"),
        embedding_provider=_required_string(payload, "embedding_provider"),
        embedding_model=_optional_string(payload.get("embedding_model")),
        embedding_revision=_optional_string(payload.get("embedding_revision")),
        embedding_artifact_sha256=_optional_string(
            payload.get("embedding_artifact_sha256")
        ),
        embedding_dimensions=_required_int(payload, "embedding_dimensions"),
        embedding_fallback_policy=_required_string(
            payload,
            "embedding_fallback_policy",
        ),
        deterministic_fallback_used=_required_bool(
            payload,
            "deterministic_fallback_used",
        ),
        embedding_retry_count=_required_int(payload, "embedding_retry_count"),
        embedding_failure_type=_optional_string(
            payload.get("embedding_failure_type")
        ),
        production_runtime_identity=_required_string(
            payload,
            "production_runtime_identity",
        ),
        input_row_count=_required_int(payload, "input_row_count"),
        cutoff_safe_row_count=_required_int(payload, "cutoff_safe_row_count"),
        audit_only_row_count=_required_int(payload, "audit_only_row_count"),
        exact_duplicate_count=_required_int(payload, "exact_duplicate_count"),
        semantic_duplicate_count=_required_int(
            payload,
            "semantic_duplicate_count",
        ),
        clusters=tuple(clusters),
        warnings=tuple(
            str(value)
            for value in payload.get("warnings", [])
            if isinstance(value, str)
        ),
    )
    covered_rows = [
        item.row_number for cluster in result.clusters for item in cluster.members
    ]
    if (
        len(covered_rows) != result.input_row_count
        or len(covered_rows) != len(set(covered_rows))
    ):
        raise ValueError("shared event clustering row coverage is incomplete")
    return result


def _required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"shared event clustering field is invalid: {key}")
    return value


def _required_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"shared event clustering field is invalid: {key}")
    return value


def _required_bool(payload: dict[str, Any], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"shared event clustering field is invalid: {key}")
    return value


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("shared event clustering similarity is invalid")
    return float(value)


def _exact_groups(items: Sequence[NewsItem]) -> list[_ExactGroup]:
    grouped: dict[str, list[NewsItem]] = defaultdict(list)
    for item in items:
        grouped[_exact_fingerprint(item)].append(item)
    groups = [
        _ExactGroup(
            fingerprint=fingerprint,
            representative=members[0],
            members=tuple(members),
            text=_semantic_text(members[0]),
            number_signature=_number_signature(members[0]),
            leading_anchor=_leading_title_anchor(members[0].title),
            predicate_terms=_predicate_terms(members[0].title),
            action_states=_action_state_signature(members[0].combined_text),
            counterparties=_counterparty_signature(members[0].combined_text),
        )
        for fingerprint, members in grouped.items()
    ]
    return sorted(groups, key=lambda group: group.representative.row_number)


async def _embed_in_batches(
    provider: EmbeddingProvider,
    texts: Sequence[str],
    *,
    batch_size: int,
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(
            await provider.embed(
                texts=list(texts[start : start + batch_size]),
                purpose="daily_event_clustering",
            )
        )
    return vectors


def _validate_vectors(vectors: Sequence[Sequence[float]], *, expected_count: int) -> None:
    if len(vectors) != expected_count:
        raise ValueError("embedding provider returned the wrong vector count")
    dimensions = {len(vector) for vector in vectors}
    if expected_count and (not dimensions or 0 in dimensions or len(dimensions) != 1):
        raise ValueError("embedding vectors must have one non-zero dimension")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        for vector in vectors
        for value in vector
    ):
        raise ValueError("embedding vectors must contain only finite values")


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _complete_link_clusters(
    groups: Sequence[_ExactGroup],
    vectors: Sequence[Sequence[float]],
    *,
    similarity_threshold: float,
    max_semantic_variants: int,
) -> list[list[int]]:
    clusters: list[list[int]] = []
    for candidate_index, candidate in enumerate(groups):
        best_cluster_index: int | None = None
        best_minimum_similarity = -1.0
        for cluster_index, cluster in enumerate(clusters):
            if len(cluster) >= max_semantic_variants:
                continue
            if not all(_structurally_compatible(candidate, groups[member_index]) for member_index in cluster):
                continue
            similarities = [_cosine(vectors[candidate_index], vectors[member_index]) for member_index in cluster]
            minimum = min(similarities)
            if minimum < similarity_threshold:
                continue
            if minimum > best_minimum_similarity:
                best_cluster_index = cluster_index
                best_minimum_similarity = minimum
        if best_cluster_index is None:
            clusters.append([candidate_index])
        else:
            clusters[best_cluster_index].append(candidate_index)
    return clusters


def _material_cluster(
    group_indexes: Sequence[int],
    *,
    exact_groups: Sequence[_ExactGroup],
    vectors: Sequence[Sequence[float]],
) -> EventCluster:
    groups = [exact_groups[index] for index in group_indexes]
    members = tuple(item for group in groups for item in group.members)
    event_ids = sorted(item.event_id for item in members)
    signature = sha256_text(canonical_json(event_ids))
    similarities = [
        _cosine(vectors[left], vectors[right])
        for offset, left in enumerate(group_indexes)
        for right in group_indexes[offset + 1 :]
    ]
    return EventCluster(
        cluster_id=stable_id("EVCL", signature, length=16),
        disposition="MATERIAL_FULL_RETRIEVAL",
        representative=members[0],
        members=members,
        exact_group_count=len(groups),
        exact_duplicate_count=sum(len(group.members) - 1 for group in groups),
        semantic_duplicate_count=max(0, len(groups) - 1),
        minimum_semantic_similarity=min(similarities) if similarities else None,
        cluster_signature_sha256=signature,
    )


def _audit_cluster(item: NewsItem) -> EventCluster:
    signature = sha256_text(canonical_json([item.event_id, "AUDIT_ONLY"]))
    return EventCluster(
        cluster_id=stable_id("EVCL-AUDIT", signature, length=16),
        disposition="AUDIT_ONLY",
        representative=item,
        members=(item,),
        exact_group_count=1,
        exact_duplicate_count=0,
        semantic_duplicate_count=0,
        minimum_semantic_similarity=None,
        cluster_signature_sha256=signature,
    )


def _structurally_compatible(left: _ExactGroup, right: _ExactGroup) -> bool:
    if _quantity_signatures_conflict(
        left.number_signature,
        right.number_signature,
    ):
        return False
    if (
        left.leading_anchor is not None
        and right.leading_anchor is not None
        and left.leading_anchor != right.leading_anchor
    ):
        return False
    if _action_states_conflict(left.action_states, right.action_states):
        return False
    if (
        left.counterparties
        and right.counterparties
        and left.counterparties.isdisjoint(right.counterparties)
    ):
        return False
    predicate_union = left.predicate_terms | right.predicate_terms
    predicate_overlap = (
        len(left.predicate_terms & right.predicate_terms) / len(predicate_union) if predicate_union else 0.0
    )
    left_terms = _terms(left.representative.title)
    right_terms = _terms(right.representative.title)
    union = left_terms | right_terms
    token_overlap = len(left_terms & right_terms) / len(union) if union else 0.0
    character_overlap = _character_bigram_overlap(left.representative.title, right.representative.title)
    return predicate_overlap >= 0.20 or token_overlap >= 0.40 or character_overlap >= 0.55


def _action_state_signature(text: str) -> frozenset[str]:
    states: set[str] = set()
    if _AFFIRMING_ACTION_PATTERN.search(text):
        states.add("AFFIRMING")
    if _REVERSING_ACTION_PATTERN.search(text):
        states.add("REVERSING")
    return frozenset(states)


def _action_states_conflict(left: frozenset[str], right: frozenset[str]) -> bool:
    return ("REVERSING" in left) != ("REVERSING" in right)


def _counterparty_signature(text: str) -> frozenset[str]:
    values = {
        match.group("name").casefold()
        for pattern in (
            _KOREAN_COUNTERPARTY_PATTERN,
            _ENGLISH_COUNTERPARTY_PATTERN,
        )
        for match in pattern.finditer(text)
    }
    return frozenset(values)


def _number_signature(item: NewsItem) -> frozenset[str]:
    return frozenset(_canonical_quantity(match) for match in _NUMBER_PATTERN.finditer(item.combined_text))


def _quantity_signatures_conflict(
    left: frozenset[str],
    right: frozenset[str],
) -> bool:
    for prefix in ("KRW:", "USD:", "PCT:"):
        left_values = {value for value in left if value.startswith(prefix)}
        right_values = {value for value in right if value.startswith(prefix)}
        if left_values and right_values and left_values != right_values:
            return True
    left_bare = {value for value in left if value.startswith("NUMBER:")}
    right_bare = {value for value in right if value.startswith("NUMBER:")}
    return bool(left_bare and right_bare and left_bare.isdisjoint(right_bare))


def _canonical_quantity(match: re.Match[str]) -> str:
    raw_number = match.group("number").replace(",", "")
    unit = (match.group("unit") or "").casefold()
    try:
        number = Decimal(raw_number)
    except InvalidOperation:
        return f"RAW:{match.group(0).casefold()}"
    won_multiplier = {
        "원": Decimal(1),
        "만": Decimal(10_000),
        "만원": Decimal(10_000),
        "억": Decimal(100_000_000),
        "억원": Decimal(100_000_000),
        "조": Decimal(1_000_000_000_000),
        "조원": Decimal(1_000_000_000_000),
    }
    if unit in won_multiplier:
        return f"KRW:{_decimal_text(number * won_multiplier[unit])}"
    if unit == "%":
        return f"PCT:{_decimal_text(number)}"
    if unit == "달러":
        return f"USD:{_decimal_text(number)}"
    return f"NUMBER:{_decimal_text(number)}"


def _decimal_text(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _embedding_provider_identity(provider: EmbeddingProvider) -> str:
    actual: Any = getattr(provider, "provider", provider)
    provider_name = type(actual).__name__
    embedding_model = getattr(actual, "embedding_model", None)
    if isinstance(embedding_model, str) and embedding_model.strip():
        return f"{provider_name}:{embedding_model.strip()}"
    model = getattr(actual, "model", None)
    if isinstance(model, str) and model.strip():
        return f"{provider_name}:{model.strip()}"
    return provider_name


def _terms(text: str) -> set[str]:
    normalized = "".join(character.casefold() if character.isalnum() else " " for character in text)
    return {term for term in normalized.split() if len(term) > 1}


def _leading_title_anchor(title: str) -> tuple[str, ...] | None:
    without_leading_labels = re.sub(r"^(?:\s*[\[({<][^\])}>]{1,30}[\])}>]\s*)+", "", title)
    without_editorial_prefix = _EDITORIAL_PREFIX_PATTERN.sub(
        "",
        without_leading_labels,
    )
    prefix = _NUMBER_PATTERN.split(without_editorial_prefix, maxsplit=1)[0]
    terms = _ordered_terms(prefix)
    if not terms:
        return None
    return tuple(terms[:2])


def _predicate_terms(title: str) -> frozenset[str]:
    terms = _ordered_terms(title)
    return frozenset(terms[1:] if len(terms) > 1 else terms)


def _ordered_terms(text: str) -> list[str]:
    normalized = "".join(character.casefold() if character.isalnum() else " " for character in text)
    return [term for term in normalized.split() if len(term) > 1]


def _character_bigram_overlap(left: str, right: str) -> float:
    left_set = _character_bigrams(left)
    right_set = _character_bigrams(right)
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 0.0


def _character_bigrams(text: str) -> set[str]:
    compact = "".join(character.casefold() for character in text if character.isalnum())
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    left_magnitude = math.sqrt(sum(value * value for value in left))
    right_magnitude = math.sqrt(sum(value * value for value in right))
    if left_magnitude == 0.0 or right_magnitude == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_magnitude * right_magnitude)


def _semantic_text(item: NewsItem) -> str:
    return "\n".join(
        [
            " ".join(item.title.casefold().split()),
            " ".join(item.body.casefold().split())[:4000],
        ]
    )


def _exact_fingerprint(item: NewsItem) -> str:
    return sha256_text(
        "\n".join(
            [
                " ".join(item.title.casefold().split()),
                " ".join(item.body.casefold().split()),
            ]
        )
    )
