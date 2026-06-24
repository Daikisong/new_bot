"""Provider protocols."""

from __future__ import annotations

from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMProvider(Protocol):
    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        """Generate free-form text."""

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        """Generate structured output validated by a Pydantic model."""

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        """Generate embeddings for retrieval."""


class EmbeddingProvider(Protocol):
    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        """Generate embeddings for retrieval."""
