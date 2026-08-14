"""Factory-verified production embedding runtime for memory consumers."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter

OPENAI_LLM_PROVIDER_ALIASES = {"openai", "responses", "openai-responses"}


def create_production_embedding_provider(
    settings: Settings,
    *,
    require_records: bool,
    provider: Any | None = None,
    provider_factory: Callable[[Settings], Any] | None = None,
    module_loader: Callable[[str], Any] = import_module,
) -> AsyncEmbeddingProviderAdapter:
    provider_name = settings.llm_provider.strip().lower()
    if provider_name == "mock" or settings.llm.provider.strip().lower() == "mock":
        raise ValueError("production memory index requires a real LLM provider")
    if (
        require_records
        and next(BrainRecordStore(settings.project_root).iter_records(), None) is None
    ):
        raise ValueError("production memory index requires normalized brain records")
    if provider_name in OPENAI_LLM_PROVIDER_ALIASES:
        if not settings.env_value("OPENAI_API_KEY"):
            raise ValueError("production memory index requires OPENAI_API_KEY")
        try:
            module = module_loader("openai")
        except ImportError as exc:
            raise ValueError(
                "production vector index rebuild requires the openai SDK"
            ) from exc
        if not hasattr(module, "AsyncOpenAI"):
            raise ValueError(
                "production memory index requires an openai SDK exposing AsyncOpenAI"
            )
    factory = provider_factory or create_llm_provider
    provider = provider if provider is not None else factory(settings)
    if isinstance(provider, DeterministicMockLLMProvider):
        raise ValueError("production memory index cannot use the mock LLM provider")
    return AsyncEmbeddingProviderAdapter(
        provider,
        embedding_method=production_embedding_method(settings, provider),
        production_capability_attested=True,
    )


def create_production_memory_index(
    settings: Settings,
    *,
    require_records: bool,
) -> ProductionMemoryIndex:
    return ProductionMemoryIndex(
        settings.project_root,
        embedding_provider=create_production_embedding_provider(
            settings,
            require_records=require_records,
        ),
        production=True,
    )


def production_embedding_method(settings: Settings, provider: Any) -> str:
    base_provider = getattr(provider, "provider", provider)
    actual_model = getattr(base_provider, "embedding_model", None)
    configured_model = settings.llm.embedding_model
    if isinstance(actual_model, str) and actual_model.strip():
        model = actual_model.strip()
        if configured_model and configured_model.strip() != model:
            raise ValueError("configured embedding model differs from the active provider")
    elif configured_model and configured_model.strip():
        model = configured_model.strip()
    else:
        raise ValueError("production embedding provider identity is unavailable")
    return f"llm_embedding:{settings.llm_provider.strip().lower()}:{model}"
