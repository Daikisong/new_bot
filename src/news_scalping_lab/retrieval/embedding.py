"""Local embedding providers for retrieval support."""

from __future__ import annotations

import math
from typing import Protocol

from news_scalping_lab.llm.base import EmbeddingProvider
from news_scalping_lab.utils import sha256_text

VECTOR_DIMENSIONS = 32
VECTOR_EMBEDDING_METHOD = "deterministic_hashing_v1"


class LocalEmbeddingProvider(EmbeddingProvider, Protocol):
    """Synchronous local embedding seam used by the on-disk vector index."""

    dimensions: int
    embedding_method: str

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate local embeddings synchronously."""


class DeterministicHashEmbeddingProvider:
    """Deterministic embedding provider for local tests and offline runs."""

    dimensions = VECTOR_DIMENSIONS
    embedding_method = VECTOR_EMBEDDING_METHOD

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [_text_vector(text, dimensions=self.dimensions) for text in texts]

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return self.embed_texts(texts)


def text_terms(text: str) -> set[str]:
    normalized = "".join(character.lower() if character.isalnum() else " " for character in text)
    return {term for term in normalized.split() if len(term) > 1}


def text_bigrams(text: str) -> set[str]:
    compact = "".join(character for character in text.lower() if character.isalnum())
    return {compact[index : index + 2] for index in range(max(0, len(compact) - 1))}


def _text_vector(text: str, *, dimensions: int) -> list[float]:
    vector = [0.0 for _ in range(dimensions)]
    features = [*text_terms(text), *text_bigrams(text)]
    for feature in features:
        digest = sha256_text(feature)
        bucket = int(digest[:8], 16) % dimensions
        sign = 1.0 if int(digest[8:10], 16) % 2 == 0 else -1.0
        vector[bucket] += sign
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return vector
    return [value / magnitude for value in vector]
