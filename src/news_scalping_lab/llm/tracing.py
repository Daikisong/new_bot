"""Trace wrapper for LLM provider calls."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from news_scalping_lab.llm.base import LLMProvider, count_provider_tokens
from news_scalping_lab.utils import (
    canonical_json,
    now_kst,
    read_json,
    sha256_text,
    stable_id,
    write_json,
)

T = TypeVar("T", bound=BaseModel)
U = TypeVar("U")
TRACE_SCHEMA_VERSION = "nslab.llm_trace.v1"
_PRE_RETRIEVAL_VARIANT_INVARIANT_PURPOSES = (
    "daily_event_clustering",
    "open_world_first_analysis",
    "news_novelty_review",
)
_CROSS_COMPILER_MAP_PURPOSES = (
    "offline_semantic_leaf",
    "offline_long_payload_map",
)


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
        compatible_checkpoint_model_configs: Sequence[dict[str, Any]] | None = None,
        resume_from_checkpoints: bool = True,
        max_retries: int = 0,
    ) -> None:
        self.provider = provider
        self.trace_dir = trace_dir
        self.checkpoint_dir = checkpoint_dir or trace_dir.parent / "checkpoints" / "llm"
        self.model_config = model_config or {"provider": type(provider).__name__}
        self.default_metadata = default_metadata or {}
        self.purpose_metadata = purpose_metadata or {}
        self.compatible_checkpoint_model_configs = _unique_model_configs(
            [self.model_config, *(compatible_checkpoint_model_configs or [])]
        )
        self.checkpoint_usage_rows: list[dict[str, Any]] = []
        self.resume_from_checkpoints = resume_from_checkpoints
        self.max_retries = max(0, max_retries)
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

    def count_tokens(self, text: str) -> int:
        return count_provider_tokens(self.provider, text)

    def configure_checkpoint_identity(
        self,
        *,
        model_config: dict[str, Any],
        default_metadata: dict[str, Any],
        compatible_checkpoint_model_configs: Sequence[dict[str, Any]],
    ) -> None:
        provider_model = getattr(self.provider, "model", None)
        provider_effort = getattr(self.provider, "reasoning_effort", None)
        if provider_model is not None and provider_model != model_config.get("model"):
            raise ValueError("wrapped LLM provider model differs from requested model")
        if provider_effort is not None and provider_effort != model_config.get(
            "reasoning_effort"
        ):
            raise ValueError("wrapped LLM provider reasoning effort differs from request")
        self.model_config = dict(model_config)
        self.default_metadata = dict(default_metadata)
        self.compatible_checkpoint_model_configs = _unique_model_configs(
            [self.model_config, *compatible_checkpoint_model_configs]
        )

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        started_at = now_kst()
        input_payload = {
            "prompt_sha256": sha256_text(prompt),
            "prompt_chars": len(prompt),
            "prompt_utf8_bytes": len(prompt.encode("utf-8")),
            "prompt_tokens_counted": self.count_tokens(prompt),
        }
        checkpoint = self._read_ok_checkpoint(operation="generate_text", purpose=purpose, input_payload=input_payload)
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
                        "prompt_tokens_estimate": self.count_tokens(prompt),
                        "completion_tokens_estimate": _estimate_tokens(output),
                    },
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                    retries=_checkpoint_retries(checkpoint),
                    retry_errors=_checkpoint_retry_errors(checkpoint),
                )
                return output
        try:
            provider_output, retries, retry_errors = await self._call_with_retries(
                lambda: self.provider.generate_text(prompt=prompt, purpose=purpose)
            )
        except _RetryExhausted as exc:
            checkpoint_id = self._write_checkpoint(
                operation="generate_text",
                purpose=purpose,
                status="error",
                input_payload=input_payload,
                error=exc.original,
                token_usage={"prompt_tokens_estimate": self.count_tokens(prompt)},
                retries=exc.retries,
                retry_errors=exc.retry_errors,
            )
            self._write_trace(
                operation="generate_text",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                token_usage={"prompt_tokens_estimate": self.count_tokens(prompt)},
                error=exc.original,
                checkpoint_id=checkpoint_id,
                retries=exc.retries,
                retry_errors=exc.retry_errors,
            )
            raise exc.original from exc
        checkpoint_id = self._write_checkpoint(
            operation="generate_text",
            purpose=purpose,
            status="ok",
            input_payload=input_payload,
            output=provider_output,
            token_usage={
                "prompt_tokens_estimate": self.count_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(provider_output),
            },
            retries=retries,
            retry_errors=retry_errors,
        )
        self._write_trace(
            operation="generate_text",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload=input_payload,
            output=provider_output,
            token_usage={
                "prompt_tokens_estimate": self.count_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(provider_output),
            },
            checkpoint_id=checkpoint_id,
            retries=retries,
            retry_errors=retry_errors,
        )
        return provider_output

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        started_at = now_kst()
        input_payload = self._structured_input_payload(prompt, response_model)
        checkpoint = self._read_ok_checkpoint(
            operation="generate_structured",
            purpose=purpose,
            input_payload=input_payload,
            response_model=response_model,
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
                        "prompt_tokens_estimate": self.count_tokens(prompt),
                        "completion_tokens_estimate": _estimate_tokens(canonical_json(output)),
                    },
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                    checkpoint_model_config=_checkpoint_model_config(
                        checkpoint,
                        self.model_config,
                    ),
                    retries=_checkpoint_retries(checkpoint),
                    retry_errors=_checkpoint_retry_errors(checkpoint),
                )
                self._record_checkpoint_usage(
                    operation="generate_structured",
                    purpose=purpose,
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                    model_config=_checkpoint_model_config(
                        checkpoint,
                        self.model_config,
                    ),
                    cache_hit=True,
                )
                return restored
        try:
            provider_output, retries, retry_errors = await self._call_with_retries(
                lambda: self.provider.generate_structured(
                    prompt=prompt,
                    response_model=response_model,
                    purpose=purpose,
                )
            )
        except _RetryExhausted as exc:
            checkpoint_id = self._write_checkpoint(
                operation="generate_structured",
                purpose=purpose,
                status="error",
                input_payload=input_payload,
                error=exc.original,
                token_usage={"prompt_tokens_estimate": self.count_tokens(prompt)},
                retries=exc.retries,
                retry_errors=exc.retry_errors,
            )
            self._write_trace(
                operation="generate_structured",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                token_usage={"prompt_tokens_estimate": self.count_tokens(prompt)},
                error=exc.original,
                checkpoint_id=checkpoint_id,
                retries=exc.retries,
                retry_errors=exc.retry_errors,
            )
            raise exc.original from exc
        json_output = provider_output.model_dump(mode="json")
        checkpoint_id = self._write_checkpoint(
            operation="generate_structured",
            purpose=purpose,
            status="ok",
            input_payload=input_payload,
            output=json_output,
            token_usage={
                "prompt_tokens_estimate": self.count_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(canonical_json(json_output)),
            },
            retries=retries,
            retry_errors=retry_errors,
        )
        self._record_checkpoint_usage(
            operation="generate_structured",
            purpose=purpose,
            checkpoint_id=checkpoint_id,
            model_config=self.model_config,
            cache_hit=False,
        )
        self._write_trace(
            operation="generate_structured",
            purpose=purpose,
            started_at=started_at,
            status="ok",
            input_payload=input_payload,
            output=json_output,
            token_usage={
                "prompt_tokens_estimate": self.count_tokens(prompt),
                "completion_tokens_estimate": _estimate_tokens(canonical_json(json_output)),
            },
            checkpoint_id=checkpoint_id,
            checkpoint_model_config=self.model_config,
            retries=retries,
            retry_errors=retry_errors,
        )
        return provider_output

    def read_matching_error_checkpoint_for_structured(
        self,
        *,
        prompt: str,
        response_model: type[T],
        purpose: str,
    ) -> dict[str, Any] | None:
        """Read only an error checkpoint for this exact request identity."""
        if not self.resume_from_checkpoints:
            return None
        operation = "generate_structured"
        input_payload = self._structured_input_payload(prompt, response_model)
        model_config = dict(self.model_config)
        path = self._checkpoint_path(
            operation=operation,
            purpose=purpose,
            input_payload=input_payload,
            model_config=model_config,
        )
        if not path.is_file():
            return None
        try:
            payload = read_json(path)
        except (OSError, ValueError, TypeError):
            return None
        expected_metadata = self._metadata_for_model_config(purpose, model_config)
        if (
            not isinstance(payload, dict)
            or payload.get("status") != "error"
            or payload.get("checkpoint_id") != path.stem
            or payload.get("operation") != operation
            or payload.get("purpose") != purpose
            or payload.get("input") != input_payload
            or payload.get("input_sha256") != sha256_text(canonical_json(input_payload))
            or payload.get("model_config") != model_config
            or payload.get("metadata") != expected_metadata
            or payload.get("output") is not None
            or payload.get("output_sha256") is not None
        ):
            return None
        return payload

    def _structured_input_payload(
        self,
        prompt: str,
        response_model: type[T],
    ) -> dict[str, Any]:
        return {
            "prompt_sha256": sha256_text(prompt),
            "prompt_chars": len(prompt),
            "prompt_utf8_bytes": len(prompt.encode("utf-8")),
            "prompt_tokens_counted": self.count_tokens(prompt),
            "response_model": response_model.__name__,
        }

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        started_at = now_kst()
        input_payload = {
            "texts_sha256": sha256_text(canonical_json(texts)),
            "text_count": len(texts),
            "total_chars": sum(len(text) for text in texts),
        }
        checkpoint = self._read_ok_checkpoint(operation="embed", purpose=purpose, input_payload=input_payload)
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
                    token_usage={"prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)},
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                    retries=_checkpoint_retries(checkpoint),
                    retry_errors=_checkpoint_retry_errors(checkpoint),
                )
                return output
        try:
            provider_output, retries, retry_errors = await self._call_with_retries(
                lambda: self.provider.embed(texts=texts, purpose=purpose)
            )
        except _RetryExhausted as exc:
            checkpoint_id = self._write_checkpoint(
                operation="embed",
                purpose=purpose,
                status="error",
                input_payload=input_payload,
                error=exc.original,
                token_usage={"prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)},
                retries=exc.retries,
                retry_errors=exc.retry_errors,
            )
            self._write_trace(
                operation="embed",
                purpose=purpose,
                started_at=started_at,
                status="error",
                input_payload=input_payload,
                token_usage={"prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)},
                error=exc.original,
                checkpoint_id=checkpoint_id,
                retries=exc.retries,
                retry_errors=exc.retry_errors,
            )
            raise exc.original from exc
        output_summary = _embedding_summary(provider_output)
        checkpoint_id = self._write_checkpoint(
            operation="embed",
            purpose=purpose,
            status="ok",
            input_payload=input_payload,
            output=provider_output,
            token_usage={"prompt_tokens_estimate": sum(_estimate_tokens(text) for text in texts)},
            retries=retries,
            retry_errors=retry_errors,
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
            retries=retries,
            retry_errors=retry_errors,
        )
        return provider_output

    async def _call_with_retries(self, call: Callable[[], Awaitable[U]]) -> tuple[U, int, list[dict[str, str]]]:
        retries = 0
        retry_errors: list[dict[str, str]] = []
        while True:
            try:
                return await call(), retries, retry_errors
            except Exception as exc:
                if retries >= self.max_retries:
                    raise _RetryExhausted(exc, retries, retry_errors) from exc
                retry_errors.append(_error_payload(exc))
                retries += 1

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
        checkpoint_model_config: dict[str, Any] | None = None,
        retries: int = 0,
        retry_errors: list[dict[str, str]] | None = None,
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
            "checkpoint_model_config": checkpoint_model_config or self.model_config,
            "tool_calls": [],
            "retries": retries,
            "retry_errors": retry_errors or [],
            "token_usage": token_usage or {},
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            **metadata,
        }
        if error is not None:
            payload["error"] = _error_payload(error)
        write_json(self.trace_dir / f"{trace_id}.json", payload)

    def _checkpoint_id(
        self,
        *,
        operation: str,
        purpose: str,
        input_payload: dict[str, Any],
        model_config: dict[str, Any] | None = None,
    ) -> str:
        checkpoint_model_config = dict(model_config or self.model_config)
        metadata = self._metadata_for_model_config(purpose, checkpoint_model_config)
        if _purpose_matches_any(
            purpose,
            _PRE_RETRIEVAL_VARIANT_INVARIANT_PURPOSES,
        ):
            checkpoint_model_config.pop("runtime_retrieval_variant", None)
        return stable_id(
            "LLMCKPT",
            canonical_json(
                {
                    "operation": operation,
                    "purpose": purpose,
                    "input": input_payload,
                    "model_config": checkpoint_model_config,
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
        model_config: dict[str, Any] | None = None,
    ) -> Path:
        return self.checkpoint_dir / (
            self._checkpoint_id(
                operation=operation,
                purpose=purpose,
                input_payload=input_payload,
                model_config=model_config,
            )
            + ".json"
        )

    def _read_ok_checkpoint(
        self,
        *,
        operation: str,
        purpose: str,
        input_payload: dict[str, Any],
        response_model: type[BaseModel] | None = None,
    ) -> dict[str, Any] | None:
        if not self.resume_from_checkpoints:
            return None
        expected_input_sha256 = sha256_text(canonical_json(input_payload))
        current_compiler = self.model_config.get("compiler_version")
        for model_config in self.compatible_checkpoint_model_configs:
            candidate_compiler = model_config.get("compiler_version")
            if candidate_compiler != current_compiler and not _purpose_prefix_matches_any(
                purpose,
                _CROSS_COMPILER_MAP_PURPOSES,
            ):
                continue
            expected_metadata = self._metadata_for_model_config(purpose, model_config)
            path = self._checkpoint_path(
                operation=operation,
                purpose=purpose,
                input_payload=input_payload,
                model_config=model_config,
            )
            if not path.is_file():
                continue
            try:
                payload = read_json(path)
            except (OSError, ValueError, TypeError):
                continue
            if (
                not isinstance(payload, dict)
                or payload.get("status") != "ok"
                or payload.get("checkpoint_id") != path.stem
                or payload.get("operation") != operation
                or payload.get("purpose") != purpose
                or payload.get("input") != input_payload
                or payload.get("input_sha256") != expected_input_sha256
                or payload.get("model_config") != model_config
                or payload.get("metadata") != expected_metadata
            ):
                continue
            output = payload.get("output")
            if output is None or payload.get("output_sha256") != sha256_text(
                canonical_json(output)
            ):
                continue
            if response_model is not None:
                try:
                    response_model.model_validate(output)
                except ValidationError:
                    continue
            return payload
        return None

    def _write_checkpoint(
        self,
        *,
        operation: str,
        purpose: str,
        status: str,
        input_payload: dict[str, Any],
        output: Any | None = None,
        error: Exception | None = None,
        token_usage: dict[str, int] | None = None,
        retries: int = 0,
        retry_errors: list[dict[str, str]] | None = None,
    ) -> str:
        checkpoint_model_config = dict(self.model_config)
        checkpoint_id = self._checkpoint_id(
            operation=operation,
            purpose=purpose,
            input_payload=input_payload,
            model_config=checkpoint_model_config,
        )
        metadata = self._metadata_for_model_config(purpose, checkpoint_model_config)
        payload: dict[str, Any] = {
            "checkpoint_id": checkpoint_id,
            "schema_version": "nslab.llm_checkpoint.v1",
            "operation": operation,
            "purpose": purpose,
            "status": status,
            "provider": type(self.provider).__name__,
            "model_config": checkpoint_model_config,
            "metadata": metadata,
            "input": input_payload,
            "input_sha256": sha256_text(canonical_json(input_payload)),
            "output": output,
            "output_sha256": sha256_text(canonical_json(output)) if output is not None else None,
            "token_usage": token_usage or {},
            "retries": retries,
            "retry_errors": retry_errors or [],
            "updated_at": now_kst().isoformat(),
        }
        if error is not None:
            payload["error"] = _error_payload(error)
        write_json(self.checkpoint_dir / f"{checkpoint_id}.json", payload)
        return checkpoint_id

    def _metadata_for(self, purpose: str) -> dict[str, Any]:
        purpose_metadata = self.purpose_metadata.get(purpose)
        if purpose_metadata is None and ".batch_" in purpose:
            purpose_metadata = self.purpose_metadata.get(purpose.split(".batch_", 1)[0])
        return {**self.default_metadata, **(purpose_metadata or {})}

    def _metadata_for_model_config(
        self,
        purpose: str,
        model_config: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = self._metadata_for(purpose)
        compiler_version = model_config.get("compiler_version")
        if isinstance(compiler_version, str) and "compiler_version" in metadata:
            metadata["compiler_version"] = compiler_version
        return metadata

    def _record_checkpoint_usage(
        self,
        *,
        operation: str,
        purpose: str,
        checkpoint_id: str,
        model_config: dict[str, Any],
        cache_hit: bool,
    ) -> None:
        self.checkpoint_usage_rows.append(
            {
                "operation": operation,
                "purpose": purpose,
                "checkpoint_id": checkpoint_id,
                "model_config": dict(model_config),
                "cache_hit": cache_hit,
            }
        )


def _unique_model_configs(
    configs: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for config in configs:
        key = canonical_json(config)
        if key not in seen:
            unique.append(dict(config))
            seen.add(key)
    return unique


def _checkpoint_model_config(
    checkpoint: dict[str, Any],
    fallback: dict[str, Any],
) -> dict[str, Any]:
    value = checkpoint.get("model_config")
    return dict(value) if isinstance(value, dict) else dict(fallback)


def _purpose_matches_any(purpose: str, prefixes: tuple[str, ...]) -> bool:
    return any(purpose == prefix or purpose.startswith(prefix + ".batch_") for prefix in prefixes)


def _purpose_prefix_matches_any(purpose: str, prefixes: tuple[str, ...]) -> bool:
    return any(purpose == prefix or purpose.startswith(prefix + ".") for prefix in prefixes)


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


def _checkpoint_retries(checkpoint: dict[str, Any]) -> int:
    value = checkpoint.get("retries")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 0


def _checkpoint_retry_errors(checkpoint: dict[str, Any]) -> list[dict[str, str]]:
    value = checkpoint.get("retry_errors")
    if not isinstance(value, list):
        return []
    errors: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        error_type = item.get("type")
        message = item.get("message")
        if isinstance(error_type, str) and isinstance(message, str):
            errors.append({"type": error_type, "message": message})
    return errors


class _RetryExhausted(Exception):
    def __init__(
        self,
        original: Exception,
        retries: int,
        retry_errors: list[dict[str, str]],
    ) -> None:
        super().__init__(str(original))
        self.original = original
        self.retries = retries
        self.retry_errors = retry_errors


def _error_payload(error: Exception) -> dict[str, str]:
    return {
        "type": type(error).__name__,
        "message": str(error),
    }
