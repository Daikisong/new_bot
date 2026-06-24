"""Optional OpenAI provider seam.

The real provider is deliberately thin. Business logic never depends on a model
name or vendor-specific behavior; callers depend on ``LLMProvider``.
"""

from __future__ import annotations

import os
from importlib import import_module
from typing import Any, TypeVar

from pydantic import BaseModel

from news_scalping_lab.llm.mock import DeterministicMockLLMProvider

T = TypeVar("T", bound=BaseModel)


class OpenAIResponsesProvider:
    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("NSLAB_OPENAI_MODEL", "gpt-5-mini")

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        client_class = _async_openai_class()
        client = client_class()
        response = await client.responses.create(model=self.model, input=prompt)
        text = getattr(response, "output_text", None)
        if isinstance(text, str):
            return text
        return str(response)

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        # A conservative fallback keeps this adapter usable before project-specific
        # structured prompts are calibrated. Production callers can replace it with
        # Responses structured output without changing pipeline code.
        mock = DeterministicMockLLMProvider()
        return await mock.generate_structured(prompt=prompt, response_model=response_model, purpose=purpose)

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        client_class = _async_openai_class()
        client = client_class()
        response = await client.embeddings.create(model="text-embedding-3-small", input=texts)
        return [item.embedding for item in response.data]


def _async_openai_class() -> Any:
    try:
        module = import_module("openai")
    except ImportError as exc:
        raise RuntimeError("install the openai extra or use NSLAB_LLM_PROVIDER=mock") from exc
    return module.AsyncOpenAI
