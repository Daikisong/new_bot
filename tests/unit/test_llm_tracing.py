from __future__ import annotations

from typing import TypeVar

import pytest
from pydantic import BaseModel

from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.tracing import TracingLLMProvider
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.utils import read_json

T = TypeVar("T", bound=BaseModel)


class FailingProvider:
    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        raise RuntimeError("text failed")

    async def generate_structured(self, *, prompt: str, response_model: type[T], purpose: str) -> T:
        raise RuntimeError("structured failed")

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        raise RuntimeError("embed failed")


@pytest.mark.asyncio
async def test_tracing_llm_provider_records_text_structured_and_embed_calls(tmp_path) -> None:
    provider = TracingLLMProvider(
        DeterministicMockLLMProvider(),
        trace_dir=tmp_path,
        model_config={"provider": "mock", "model": "deterministic"},
        default_metadata={"prompt_version": "test-v1"},
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
    assert structured["status"] == "ok"
    assert structured["input"]["response_model"] == "SemanticResearchDraft"
    assert structured["output"]["trade_date"] == "2030-01-10"
    assert structured["prompt_version"] == "test-v1"
    assert structured["model_config"] == {"provider": "mock", "model": "deterministic"}
    assert structured["token_usage"]["prompt_tokens_estimate"] > 0
    assert structured["tool_calls"] == []
    assert structured["retries"] == 0


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
