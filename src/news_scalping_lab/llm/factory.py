"""LLM provider factory."""

from __future__ import annotations

from news_scalping_lab.config import Settings
from news_scalping_lab.llm.base import LLMProvider
from news_scalping_lab.llm.codex_oauth_provider import CodexOAuthProvider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.llm.openai_provider import OpenAIResponsesProvider


def create_llm_provider(settings: Settings) -> LLMProvider:
    provider = settings.llm_provider.strip().lower()
    if provider == "mock":
        return DeterministicMockLLMProvider(
            model=settings.llm.model,
            reasoning_effort=settings.llm.reasoning_effort,
            max_output_tokens=settings.llm.max_output_tokens,
        )
    if provider in {"openai", "responses", "openai-responses"}:
        return OpenAIResponsesProvider(
            model=settings.llm.model,
            embedding_model=settings.llm.embedding_model,
            reasoning_effort=settings.llm.reasoning_effort,
            max_output_tokens=settings.llm.max_output_tokens,
            api_key=settings.env_value("OPENAI_API_KEY"),
        )
    if provider in {"codex-oauth", "codex_oauth"}:
        return CodexOAuthProvider(
            command=settings.codex_command,
            model=settings.llm.model,
            reasoning_effort=(
                settings.llm.reasoning_effort or settings.codex_reasoning_effort
            ),
            max_output_tokens=settings.llm.max_output_tokens,
            structured_repair_retries=settings.llm.max_retries,
        )
    raise ValueError(f"unsupported LLM provider: {settings.llm_provider}")
