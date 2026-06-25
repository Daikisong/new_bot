from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel

from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.utils import canonical_json, read_json, sha256_text

T = TypeVar("T", bound=BaseModel)


class FailingProvider:
    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        raise RuntimeError("text failed")

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        raise RuntimeError("structured failed")

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        raise RuntimeError("embed failed")


class CountingProvider:
    def __init__(self) -> None:
        self.structured_calls = 0

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        return f"text:{purpose}:{prompt}"

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        self.structured_calls += 1
        return await DeterministicMockLLMProvider().generate_structured(
            prompt=prompt,
            response_model=response_model,
            purpose=purpose,
        )

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return await DeterministicMockLLMProvider().embed(texts=texts, purpose=purpose)


class FlakyTextProvider:
    def __init__(self, failures_before_success: int) -> None:
        self.failures_before_success = failures_before_success
        self.calls = 0

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise RuntimeError(f"temporary text failure {self.calls}")
        return f"recovered:{purpose}:{prompt}"

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        return await DeterministicMockLLMProvider().generate_structured(
            prompt=prompt,
            response_model=response_model,
            purpose=purpose,
        )

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return await DeterministicMockLLMProvider().embed(texts=texts, purpose=purpose)


@pytest.mark.asyncio
async def test_tracing_llm_provider_records_text_structured_and_embed_calls(tmp_path) -> None:
    provider = TracingLLMProvider(
        DeterministicMockLLMProvider(),
        trace_dir=tmp_path,
        model_config={"provider": "mock", "model": "deterministic"},
        default_metadata={"prompt_version": "test-v1"},
        purpose_metadata={"trace.structured": {"prompt_version": "structured-v2"}},
    )

    text = await provider.generate_text(prompt="hello", purpose="trace.text")
    draft = await provider.generate_structured(
        prompt="Research 2030-01-10\n---SOURCE_TEXT---\nnotes",
        response_model=SemanticResearchDraft,
        purpose="trace.structured",
    )
    vectors = await provider.embed(texts=["alpha", "beta"], purpose="trace.embed")

    assert text.startswith("mock:trace.text")
    assert draft.trade_date.isoformat() == "2030-01-10"
    assert len(vectors) == 2

    traces = [read_json(path) for path in sorted(tmp_path.glob("TRACE-*.json"))]
    assert {trace["operation"] for trace in traces} == {
        "generate_text",
        "generate_structured",
        "embed",
    }
    structured = next(trace for trace in traces if trace["operation"] == "generate_structured")
    text_trace = next(trace for trace in traces if trace["operation"] == "generate_text")
    embed_trace = next(trace for trace in traces if trace["operation"] == "embed")
    assert all(trace["schema_version"] == "nslab.llm_trace.v1" for trace in traces)
    assert structured["status"] == "ok"
    assert structured["input"]["response_model"] == "SemanticResearchDraft"
    assert structured["output"]["trade_date"] == "2030-01-10"
    assert structured["metadata"]["prompt_version"] == "structured-v2"
    assert structured["prompt_version"] == "structured-v2"
    assert text_trace["metadata"]["prompt_version"] == "test-v1"
    assert text_trace["prompt_version"] == "test-v1"
    assert structured["model_config"] == {"provider": "mock", "model": "deterministic"}
    assert structured["token_usage"]["prompt_tokens_estimate"] > 0
    assert structured["token_usage"]["completion_tokens_estimate"] > 0
    assert text_trace["token_usage"]["completion_tokens_estimate"] > 0
    assert embed_trace["input"]["text_count"] == 2
    assert embed_trace["output"] == {
        "vector_count": 2,
        "dimensions": len(vectors[0]),
        "vectors_sha256": sha256_text(canonical_json(vectors)),
    }
    assert embed_trace["output_sha256"] == sha256_text(canonical_json(embed_trace["output"]))
    assert structured["tool_calls"] == []
    assert structured["retries"] == 0
    assert structured["retry_errors"] == []


@pytest.mark.asyncio
async def test_tracing_llm_provider_records_failed_calls(tmp_path) -> None:
    provider = TracingLLMProvider(FailingProvider(), trace_dir=tmp_path)

    with pytest.raises(RuntimeError, match="structured failed"):
        await provider.generate_structured(
            prompt="bad",
            response_model=SemanticResearchDraft,
            purpose="trace.failure",
        )

    traces = [read_json(path) for path in sorted(tmp_path.glob("TRACE-*.json"))]
    assert len(traces) == 1
    trace = traces[0]
    assert trace["status"] == "error"
    assert trace["operation"] == "generate_structured"
    assert trace["error"]["type"] == "RuntimeError"
    assert trace["error"]["message"] == "structured failed"
    assert trace["output"] is None
    assert trace["token_usage"]["prompt_tokens_estimate"] > 0


@pytest.mark.asyncio
async def test_tracing_llm_provider_resumes_successful_structured_checkpoint(tmp_path) -> None:
    provider_impl = CountingProvider()
    provider = TracingLLMProvider(
        provider_impl,
        trace_dir=tmp_path / "traces",
        checkpoint_dir=tmp_path / "checkpoints",
        default_metadata={"prompt_version": "checkpoint-v1"},
    )

    first = await provider.generate_structured(
        prompt="Research 2030-01-10\n---SOURCE_TEXT---\ncheckpoint notes",
        response_model=SemanticResearchDraft,
        purpose="trace.checkpoint",
    )
    second = await provider.generate_structured(
        prompt="Research 2030-01-10\n---SOURCE_TEXT---\ncheckpoint notes",
        response_model=SemanticResearchDraft,
        purpose="trace.checkpoint",
    )

    assert provider_impl.structured_calls == 1
    assert second.model_dump(mode="json") == first.model_dump(mode="json")
    checkpoints = [read_json(path) for path in sorted((tmp_path / "checkpoints").glob("*.json"))]
    assert len(checkpoints) == 1
    assert checkpoints[0]["status"] == "ok"
    assert checkpoints[0]["metadata"]["prompt_version"] == "checkpoint-v1"
    traces = sorted(
        [read_json(path) for path in (tmp_path / "traces").glob("TRACE-*.json")],
        key=lambda trace: str(trace["started_at"]),
    )
    assert [trace["status"] for trace in traces] == ["ok", "checkpoint_hit"]
    assert traces[1]["checkpoint_id"] == checkpoints[0]["checkpoint_id"]


@pytest.mark.asyncio
async def test_tracing_llm_provider_writes_error_checkpoint(tmp_path) -> None:
    provider = TracingLLMProvider(
        FailingProvider(),
        trace_dir=tmp_path / "traces",
        checkpoint_dir=tmp_path / "checkpoints",
    )

    with pytest.raises(RuntimeError, match="text failed"):
        await provider.generate_text(prompt="bad", purpose="trace.error_checkpoint")

    checkpoints = [read_json(path) for path in sorted((tmp_path / "checkpoints").glob("*.json"))]
    assert len(checkpoints) == 1
    assert checkpoints[0]["status"] == "error"
    assert checkpoints[0]["error"]["message"] == "text failed"
    traces = [read_json(path) for path in sorted((tmp_path / "traces").glob("TRACE-*.json"))]
    assert traces[0]["status"] == "error"
    assert traces[0]["checkpoint_id"] == checkpoints[0]["checkpoint_id"]


@pytest.mark.asyncio
async def test_tracing_llm_provider_records_successful_retry_count(tmp_path) -> None:
    provider_impl = FlakyTextProvider(failures_before_success=2)
    provider = TracingLLMProvider(
        provider_impl,
        trace_dir=tmp_path / "traces",
        checkpoint_dir=tmp_path / "checkpoints",
        max_retries=2,
    )

    text = await provider.generate_text(prompt="recover", purpose="trace.retry")
    replayed_text = await provider.generate_text(prompt="recover", purpose="trace.retry")

    assert text == "recovered:trace.retry:recover"
    assert replayed_text == text
    assert provider_impl.calls == 3
    traces = sorted(
        [read_json(path) for path in (tmp_path / "traces").glob("TRACE-*.json")],
        key=lambda trace: str(trace["started_at"]),
    )
    assert [trace["status"] for trace in traces] == ["ok", "checkpoint_hit"]
    assert traces[0]["retries"] == 2
    assert traces[0]["retry_errors"] == [
        {"type": "RuntimeError", "message": "temporary text failure 1"},
        {"type": "RuntimeError", "message": "temporary text failure 2"},
    ]
    assert traces[1]["retries"] == 2
    assert traces[1]["retry_errors"] == traces[0]["retry_errors"]
    checkpoint = read_json(next((tmp_path / "checkpoints").glob("LLMCKPT-*.json")))
    assert checkpoint["retries"] == 2
    assert checkpoint["retry_errors"] == traces[0]["retry_errors"]


@pytest.mark.asyncio
async def test_tracing_llm_provider_records_exhausted_retry_count(tmp_path) -> None:
    provider_impl = FlakyTextProvider(failures_before_success=3)
    provider = TracingLLMProvider(
        provider_impl,
        trace_dir=tmp_path / "traces",
        checkpoint_dir=tmp_path / "checkpoints",
        max_retries=2,
    )

    with pytest.raises(RuntimeError, match="temporary text failure 3"):
        await provider.generate_text(prompt="still bad", purpose="trace.retry_failure")

    assert provider_impl.calls == 3
    traces = [read_json(path) for path in sorted((tmp_path / "traces").glob("TRACE-*.json"))]
    assert len(traces) == 1
    assert traces[0]["status"] == "error"
    assert traces[0]["retries"] == 2
    assert traces[0]["retry_errors"] == [
        {"type": "RuntimeError", "message": "temporary text failure 1"},
        {"type": "RuntimeError", "message": "temporary text failure 2"},
    ]
    assert traces[0]["error"]["message"] == "temporary text failure 3"
