"""Trace wrapper for LLM provider calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.utils import canonical_json, now_kst, sha256_text, stable_id, write_json

T = TypeVar("T", bound=BaseModel)


class TracingLLMProvider:
    """Wrap an LLM provider and persist reproducible call traces."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        trace_dir: Path,
        model_config: dict[str, Any] | None = None,
        default_metadata: dict[str, Any] | None = None,
    ) -> None:
        self.provider = provider
        self.trace_dir = trace_dir
        self.model_config = model_config or {"provider": type(provider).__name__}
        self.default_metadata = default_metadata or {}
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        started_at = now_kst()
        try:
            output = await self.provider.generate_text(prompt=prompt, purpose=purpose)
        except Exception as exc:
            self._write_trace(
                operation="generate_text",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload={"prompt_sha256": sha256_text(prompt), "prompt_chars": len(prompt)},
                error=exc,
            )
            raise
        self._write_trace(
            operation="generate_text",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload={"prompt_sha256": sha256_text(prompt), "prompt_chars": len(prompt)},
            output=output,
            token_usage={
                "prompt_tokens_estimate": _estimate_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(output),
            },
        )
        return output

    async def generate_structured(
        self, *, prompt: str, response_model: type[T], purpose: str
    ) -> T:
        started_at = now_kst()
        input_payload = {
            "prompt_sha256": sha256_text(prompt),
            "prompt_chars": len(prompt),
            "response_model": response_model.__name__,
        }
        try:
            output = await self.provider.generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )
        except Exception as exc:
            self._write_trace(
                operation="generate_structured",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                error=exc,
            )
            raise
        json_output = output.model_dump(mode="json")
        self._write_trace(
            operation="generate_structured",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload=input_payload,
            output=json_output,
            token_usage={
                "prompt_tokens_estimate": _estimate_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(canonical_json(json_output)),
            },
        )
        return output

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        started_at = now_kst()
        input_payload = {
            "texts_sha256": sha256_text(canonical_json(texts)),
            "text_count": len(texts),
            "total_chars": sum(len(text) for text in texts),
        }
        try:
            output = await self.provider.embed(texts=texts, purpose=purpose)
        except Exception as exc:
            self._write_trace(
                operation="embed",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                error=exc,
            )
            raise
        output_summary = {
            "vector_count": len(output),
            "dimensions": len(output[0]) if output else 0,
            "vectors_sha256": sha256_text(canonical_json(output)),
        }
        self._write_trace(
            operation="embed",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload=input_payload,
            output=output_summary,
            token_usage={"prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)},
        )
        return output

    def _write_trace(
        self,
        *,
        operation: str,
        purpose: str,
        started_at: Any,
        status: str,
        input_payload: dict[str, Any],
        output: Any | None = None,
        token_usage: dict[str, int] | None = None,
        error: Exception | None = None,
    ) -> None:
        finished_at = now_kst()
        trace_seed = canonical_json(
            {
                "operation": operation,
                "purpose": purpose,
                "input": input_payload,
                "started_at": started_at.isoformat(),
            }
        )
        trace_id = stable_id("TRACE", trace_seed)
        payload: dict[str, Any] = {
            "trace_id": trace_id,
            "operation": operation,
            "purpose": purpose,
            "status": status,
            "provider": type(self.provider).__name__,
            "model_config": self.model_config,
            "input": input_payload,
            "input_sha256": sha256_text(canonical_json(input_payload)),
            "output": output,
            "output_sha256": sha256_text(canonical_json(output)) if output is not None else None,
            "tool_calls": [],
            "retries": 0,
            "token_usage": token_usage or {},
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            **self.default_metadata,
        }
        if error is not None:
            payload["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
        write_json(self.trace_dir / f"{trace_id}.json", payload)


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0
