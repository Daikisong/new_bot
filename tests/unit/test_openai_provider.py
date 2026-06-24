from __future__ import annotations

import sys
import types
from datetime import date, datetime, time
from typing import Any

import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.openai_provider import OpenAIResponsesProvider
from news_scalping_lab.research_import.semantic import SemanticResearchDraft
from news_scalping_lab.utils import KST


class FakeResponses:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def create(self, *, model: str, input: str) -> object:
        self.calls.append({"method": "create", "model": model, "input": input})
        return types.SimpleNamespace(output_text="fake text")

    async def parse(
        self,
        *,
        model: str,
        input: list[dict[str, str]],
        text_format: type[SemanticResearchDraft],
    ) -> object:
        self.calls.append(
            {
                "method": "parse",
                "model": model,
                "input": input,
                "text_format": text_format,
            }
        )
        parsed = SemanticResearchDraft(
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime.combine(date(2030, 1, 10), time(8, 59, 59), tzinfo=KST),
            summary="Parsed by fake OpenAI responses API.",
            open_world_mechanisms=["api structured output -> canonical draft"],
        )
        return types.SimpleNamespace(output_parsed=parsed)


class FakeEmbeddings:
    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.calls = calls

    async def create(self, *, model: str, input: list[str]) -> object:
        self.calls.append({"method": "embed", "model": model, "input": input})
        return types.SimpleNamespace(
            data=[
                types.SimpleNamespace(embedding=[0.1, 0.2]),
                types.SimpleNamespace(embedding=[0.3, 0.4]),
            ]
        )


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class FakeAsyncOpenAI:
        def __init__(self) -> None:
            self.responses = FakeResponses(calls)
            self.embeddings = FakeEmbeddings(calls)

    module = types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI, __version__="fake")
    monkeypatch.setitem(sys.modules, "openai", module)
    return calls


def _install_fake_openai_with_chat_parse(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class FakeChatCompletions:
        async def parse(
            self,
            *,
            model: str,
            messages: list[dict[str, str]],
            response_format: type[SemanticResearchDraft],
        ) -> object:
            calls.append(
                {
                    "method": "chat_parse",
                    "model": model,
                    "messages": messages,
                    "response_format": response_format,
                }
            )
            parsed = SemanticResearchDraft(
                trade_date=date(2030, 1, 11),
                cutoff_at=datetime.combine(date(2030, 1, 11), time(8, 59, 59), tzinfo=KST),
                summary="Parsed by fake chat completions API.",
                open_world_mechanisms=["chat structured output -> canonical draft"],
            )
            return types.SimpleNamespace(
                choices=[types.SimpleNamespace(message=types.SimpleNamespace(parsed=parsed))]
            )

    class FakeAsyncOpenAI:
        def __init__(self) -> None:
            self.responses = types.SimpleNamespace()
            self.beta = types.SimpleNamespace(
                chat=types.SimpleNamespace(completions=FakeChatCompletions())
            )

    module = types.SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI, __version__="fake")
    monkeypatch.setitem(sys.modules, "openai", module)
    return calls


@pytest.mark.asyncio
async def test_openai_provider_uses_responses_parse_for_structured_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_openai(monkeypatch)
    provider = OpenAIResponsesProvider(model="gpt-test", embedding_model="embed-test")

    draft = await provider.generate_structured(
        prompt="semantic import source",
        response_model=SemanticResearchDraft,
        purpose="research_import.semantic",
    )
    text = await provider.generate_text(prompt="hello", purpose="text")
    vectors = await provider.embed(texts=["a", "b"], purpose="embed")

    assert draft.trade_date == date(2030, 1, 10)
    assert text == "fake text"
    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    parse_call = calls[0]
    assert parse_call["method"] == "parse"
    assert parse_call["model"] == "gpt-test"
    assert parse_call["text_format"] is SemanticResearchDraft
    assert "semantic import source" in parse_call["input"][1]["content"]
    assert calls[2] == {"method": "embed", "model": "embed-test", "input": ["a", "b"]}


@pytest.mark.asyncio
async def test_openai_provider_falls_back_to_chat_parse_when_responses_parse_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_fake_openai_with_chat_parse(monkeypatch)
    provider = OpenAIResponsesProvider(model="gpt-test")

    draft = await provider.generate_structured(
        prompt="semantic import source",
        response_model=SemanticResearchDraft,
        purpose="research_import.semantic",
    )

    assert draft.trade_date == date(2030, 1, 11)
    assert calls[0]["method"] == "chat_parse"
    assert calls[0]["response_format"] is SemanticResearchDraft
    assert "semantic import source" in calls[0]["messages"][1]["content"]


def test_llm_factory_selects_openai_provider() -> None:
    settings = Settings(llm_provider="openai")
    provider = create_llm_provider(settings)

    assert isinstance(provider, OpenAIResponsesProvider)


def test_llm_factory_rejects_unknown_provider() -> None:
    settings = Settings(llm_provider="unknown")

    with pytest.raises(ValueError, match="unsupported LLM provider"):
        create_llm_provider(settings)
