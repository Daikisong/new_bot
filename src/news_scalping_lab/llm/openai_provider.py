"""Optional OpenAI provider seam.

The real provider is deliberately thin. Business logic never depends on a model
name or vendor-specific behavior; callers depend on ``LLMProvider``.
"""

from __future__ import annotations

import json
import os
from importlib import import_module
from typing import Any, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class OpenAIResponsesProvider:
    def __init__(self, model: str | None = None, embedding_model: str | None = None) -> None:
        self.model = model or os.getenv("NSLAB_OPENAI_MODEL", "gpt-5-mini")
        self.embedding_model = embedding_model or os.getenv(
            "NSLAB_OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
        )

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        client_class = _async_openai_class()
        client = client_class()
        response = await client.responses.create(model=self.model, input=prompt)
        text = getattr(response, "output_text", None)
        if isinstance(text, str):
            return text
        return str(response)

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        client_class = _async_openai_class()
        client = client_class()
        responses = getattr(client, "responses", None)
        parse = getattr(responses, "parse", None)
        system_message = (
            "Return only data matching the requested Pydantic schema. "
            "Do not add prose outside the structured output."
        )
        output_text: str | None = None
        if parse is not None:
            response = await parse(
                model=self.model,
                input=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": prompt},
                ],
                text_format=response_model,
            )
            parsed = getattr(response, "output_parsed", None)
            response_output_text = getattr(response, "output_text", None)
            output_text = response_output_text if isinstance(response_output_text, str) else None
        else:
            parsed = await self._generate_structured_via_chat_parse(
                client=client,
                prompt=prompt,
                system_message=system_message,
                response_model=response_model,
            )
        if parsed is None:
            if output_text is not None and output_text.strip():
                return response_model.model_validate(json.loads(output_text))
            raise RuntimeError("OpenAI structured response did not include parsed output")
        if isinstance(parsed, response_model):
            return parsed
        return response_model.model_validate(parsed)

    async def _generate_structured_via_chat_parse(
        self,
        *,
        client: Any,
        prompt: str,
        system_message: str,
        response_model: type[T],
    ) -> Any:
        beta = getattr(client, "beta", None)
        chat = getattr(beta, "chat", None)
        completions = getattr(chat, "completions", None)
        parse = getattr(completions, "parse", None)
        if parse is None:
            raise RuntimeError(
                "installed openai SDK exposes neither responses.parse nor chat.completions.parse; "
                "upgrade the openai extra"
            )
        completion = await parse(
            model=self.model,
            messages=[
                {"role": "system", "content": system_message},
                {"role": "user", "content": prompt},
            ],
            response_format=response_model,
        )
        choices = getattr(completion, "choices", [])
        if not choices:
            raise RuntimeError("OpenAI structured response did not include choices")
        message = getattr(choices[0], "message", None)
        return getattr(message, "parsed", None)

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        client_class = _async_openai_class()
        client = client_class()
        response = await client.embeddings.create(model=self.embedding_model, input=texts)
        return [item.embedding for item in response.data]


def _async_openai_class() -> Any:
    try:
        module = import_module("openai")
    except ImportError as exc:
        raise RuntimeError("install the openai extra or use NSLAB_LLM_PROVIDER=mock") from exc
    return module.AsyncOpenAI
