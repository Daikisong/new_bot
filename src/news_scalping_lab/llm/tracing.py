"""Trace wrapper for LLM provider calls."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.utils import canonical_json, now_kst, read_json, sha256_text, stable_id, write_json

T = TypeVar("T", bound=BaseModel)
TRACE_SCHEMA_VERSION = "nslab.llm_trace.v1"


class TracingLLMProvider:
    """Wrap an LLM provider and persist reproducible call traces."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        trace_dir: Path,
        checkpoint_dir: Path | None = None,
        model_config: dict[str, Any] | None = None,
        default_metadata: dict[str, Any] | None = None,
        purpose_metadata: dict[str, dict[str, Any]] | None = None,
        resume_from_checkpoints: bool = True,
    ) -> None:
        self.provider = provider
        self.trace_dir = trace_dir
        self.checkpoint_dir = checkpoint_dir or trace_dir.parent / "checkpoints" / "llm"
        self.model_config = model_config or {"provider": type(provider).__name__}
        self.default_metadata = default_metadata or {}
        self.purpose_metadata = purpose_metadata or {}
        self.resume_from_checkpoints = resume_from_checkpoints
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        started_at = now_kst()
        input_payload = {"prompt_sha256": sha256_text(prompt), "prompt_chars": len(prompt)}
        checkpoint = self._read_ok_checkpoint(
            operation="generate_text", purpose=purpose, input_payload=input_payload
        )
        if checkpoint is not None:
            output = checkpoint.get("output")
            if isinstance(output, str):
                self._write_trace(
                    operation="generate_text",
                    purpose=purpose,
                    started_at=started_at,
                    status="checkpoint_hit",
                    input_payload=input_payload,
                    output=output,
                    token_usage={
                        "prompt_tokens_estimate": _estimate_tokens(prompt),
                        "completion_tokens_estimate": _estimate_tokens(output),
                    },
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                )
                return output
        try:
            output = await self.provider.generate_text(prompt=prompt, purpose=purpose)
        except Exception as exc:
            checkpoint_id = self._write_checkpoint(
                operation="generate_text",
                purpose=purpose,
                status="error",
                input_payload=input_payload,
                error=exc,
            )
            self._write_trace(
                operation="generate_text",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                token_usage={"prompt_tokens_estimate": _estimate_tokens(prompt)},
                error=exc,
                checkpoint_id=checkpoint_id,
            )
            raise
        checkpoint_id = self._write_checkpoint(
            operation="generate_text",
            purpose=purpose,
            status="ok",
            input_payload=input_payload,
            output=output,
        )
        self._write_trace(
            operation="generate_text",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload=input_payload,
            output=output,
            token_usage={
                "prompt_tokens_estimate": _estimate_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(output),
            },
            checkpoint_id=checkpoint_id,
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
        checkpoint = self._read_ok_checkpoint(
            operation="generate_structured",
            purpose=purpose,
            input_payload=input_payload,
        )
        if checkpoint is not None:
            output = checkpoint.get("output")
            if isinstance(output, dict):
                restored = response_model.model_validate(output)
                self._write_trace(
                    operation="generate_structured",
                    purpose=purpose,
                    started_at=started_at,
                    status="checkpoint_hit",
                    input_payload=input_payload,
                    output=output,
                    token_usage={
                        "prompt_tokens_estimate": _estimate_tokens(prompt),
                        "completion_tokens_estimate": _estimate_tokens(canonical_json(output)),
                    },
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                )
                return restored
        try:
            output = await self.provider.generate_structured(
                prompt=prompt,
                response_model=response_model,
                purpose=purpose,
            )
        except Exception as exc:
            checkpoint_id = self._write_checkpoint(
                operation="generate_structured",
                purpose=purpose,
                status="error",
                input_payload=input_payload,
                error=exc,
            )
            self._write_trace(
                operation="generate_structured",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                token_usage={"prompt_tokens_estimate": _estimate_tokens(prompt)},
                error=exc,
                checkpoint_id=checkpoint_id,
            )
            raise
        json_output = output.model_dump(mode="json")
        checkpoint_id = self._write_checkpoint(
            operation="generate_structured",
            purpose=purpose,
            status="ok",
            input_payload=input_payload,
            output=json_output,
        )
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
            checkpoint_id=checkpoint_id,
        )
        return output

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        started_at = now_kst()
        input_payload = {
            "texts_sha256": sha256_text(canonical_json(texts)),
            "text_count": len(texts),
            "total_chars": sum(len(text) for text in texts),
        }
        checkpoint = self._read_ok_checkpoint(
            operation="embed", purpose=purpose, input_payload=input_payload
        )
        if checkpoint is not None:
            output = _restore_vectors(checkpoint.get("output"))
            if output is not None:
                output_summary = _embedding_summary(output)
                self._write_trace(
                    operation="embed",
                    purpose=purpose,
                    started_at=started_at,
                    status="checkpoint_hit",
                    input_payload=input_payload,
                    output=output_summary,
                    token_usage={
                        "prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)
                    },
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                )
                return output
        try:
            output = await self.provider.embed(texts=texts, purpose=purpose)
        except Exception as exc:
            checkpoint_id = self._write_checkpoint(
                operation="embed",
                purpose=purpose,
                status="error",
                input_payload=input_payload,
                error=exc,
            )
            self._write_trace(
                operation="embed",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                token_usage={
                    "prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)
                },
                error=exc,
                checkpoint_id=checkpoint_id,
            )
            raise
        output_summary = _embedding_summary(output)
        checkpoint_id = self._write_checkpoint(
            operation="embed",
            purpose=purpose,
            status="ok",
            input_payload=input_payload,
            output=output,
        )
        self._write_trace(
            operation="embed",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload=input_payload,
            output=output_summary,
            token_usage={"prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)},
            checkpoint_id=checkpoint_id,
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
        checkpoint_id: str | None = None,
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
        metadata = self._metadata_for(purpose)
        payload: dict[str, Any] = {
            "schema_version": TRACE_SCHEMA_VERSION,
            "trace_id": trace_id,
            "operation": operation,
            "purpose": purpose,
            "status": status,
            "provider": type(self.provider).__name__,
            "model_config": self.model_config,
            "metadata": metadata,
            "input": input_payload,
            "input_sha256": sha256_text(canonical_json(input_payload)),
            "output": output,
            "output_sha256": sha256_text(canonical_json(output)) if output is not None else None,
            "checkpoint_id": checkpoint_id,
            "tool_calls": [],
            "retries": 0,
            "token_usage": token_usage or {},
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            **metadata,
        }
        if error is not None:
            payload["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
        write_json(self.trace_dir / f"{trace_id}.json", payload)

    def _checkpoint_id(
        self,
        *,
        operation: str,
        purpose: str,
        input_payload: dict[str, Any],
    ) -> str:
        metadata = self._metadata_for(purpose)
        return stable_id(
            "LLMCKPT",
            canonical_json(
                {
                    "operation": operation,
                    "purpose": purpose,
                    "input": input_payload,
                    "model_config": self.model_config,
                    "metadata": metadata,
                }
            ),
            length=16,
        )

    def _checkpoint_path(
        self,
        *,
        operation: str,
        purpose: str,
        input_payload: dict[str, Any],
    ) -> Path:
        return self.checkpoint_dir / (
            self._checkpoint_id(
                operation=operation,
                purpose=purpose,
                input_payload=input_payload,
            )
            + ".json"
        )

    def _read_ok_checkpoint(
        self,
        *,
        operation: str,
        purpose: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not self.resume_from_checkpoints:
            return None
        path = self._checkpoint_path(
            operation=operation,
            purpose=purpose,
            input_payload=input_payload,
        )
        if not path.exists():
            return None
        payload = read_json(path)
        if not isinstance(payload, dict) or payload.get("status") != "ok":
            return None
        return payload

    def _write_checkpoint(
        self,
        *,
        operation: str,
        purpose: str,
        status: str,
        input_payload: dict[str, Any],
        output: Any | None = None,
        error: Exception | None = None,
    ) -> str:
        checkpoint_id = self._checkpoint_id(
            operation=operation,
            purpose=purpose,
            input_payload=input_payload,
        )
        metadata = self._metadata_for(purpose)
        payload: dict[str, Any] = {
            "checkpoint_id": checkpoint_id,
            "schema_version": "nslab.llm_checkpoint.v1",
            "operation": operation,
            "purpose": purpose,
            "status": status,
            "provider": type(self.provider).__name__,
            "model_config": self.model_config,
            "metadata": metadata,
            "input": input_payload,
            "input_sha256": sha256_text(canonical_json(input_payload)),
            "output": output,
            "output_sha256": sha256_text(canonical_json(output)) if output is not None else None,
            "updated_at": now_kst().isoformat(),
        }
        if error is not None:
            payload["error"] = {
                "type": type(error).__name__,
                "message": str(error),
            }
        write_json(self.checkpoint_dir / f"{checkpoint_id}.json", payload)
        return checkpoint_id

    def _metadata_for(self, purpose: str) -> dict[str, Any]:
        return {**self.default_metadata, **self.purpose_metadata.get(purpose, {})}


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def _embedding_summary(output: list[list[float]]) -> dict[str, Any]:
    return {
        "vector_count": len(output),
        "dimensions": len(output[0]) if output else 0,
        "vectors_sha256": sha256_text(canonical_json(output)),
    }


def _restore_vectors(value: Any) -> list[list[float]] | None:
    if not isinstance(value, list):
        return None
    restored: list[list[float]] = []
    for item in value:
        if not isinstance(item, list):
            return None
        row: list[float] = []
        for element in item:
            if not isinstance(element, (int, float)):
                return None
            row.append(float(element))
        restored.append(row)
    return restored
