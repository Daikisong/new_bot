"""Local embedding providers for retrieval support."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Coroutine
from typing import Any, Protocol

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


class AsyncEmbeddingProviderAdapter:
    """Synchronous adapter for production LLM embedding providers."""

    def __init__(
        self,
        provider: EmbeddingProvider,
        *,
        embedding_method: str,
    ) -> None:
        self.provider = provider
        self.embedding_method = embedding_method
        self.dimensions = 0

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        _ensure_no_running_loop()
        vectors = _run_embedding(
            self.provider.embed(texts=texts, purpose="vector_index.rebuild")
        )
        if vectors:
            self.dimensions = len(vectors[0])
        return vectors

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return await self.provider.embed(texts=texts, purpose=purpose)


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


def _ensure_no_running_loop() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError(
        "production vector index rebuild cannot run inside an active asyncio event loop"
    )


def _run_embedding(coro: Coroutine[Any, Any, list[list[float]]]) -> list[list[float]]:
    return asyncio.run(coro)
