"""LLM provider factory."""

from __future__ import annotations

from news_scalping_lab.config import Settings
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.openai_provider import OpenAIResponsesProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.strip().lower()
    if provider == "mock":
        return DeterministicMockLLMProvider()
    if provider in {"openai", "responses", "openai-responses"}:
        return OpenAIResponsesProvider()
    raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")
