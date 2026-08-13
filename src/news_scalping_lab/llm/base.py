"""Provider protocols."""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

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


TOKEN_COUNTING_VERSION = "provider_tokenizer_or_utf8_upper_bound.v1"


def conservative_token_upper_bound(text: str) -> int:
    """Return a tokenizer-independent upper bound for UTF-8 model input."""

    return max(1, len(text.encode("utf-8"))) if text else 0


def count_provider_tokens(provider: Any, text: str) -> int:
    """Use a provider tokenizer, falling back to a conservative byte bound."""

    counter = getattr(provider, "count_tokens", None)
    if callable(counter):
        count = counter(text)
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
    return conservative_token_upper_bound(text)
