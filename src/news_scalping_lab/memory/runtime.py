"""Factory-verified production embedding runtime for memory consumers."""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.llm.factory import create_llm_provider
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter
from news_scalping_lab.retrieval.production_embedding import (
    ProductionEmbeddingUnavailableError,
    configured_embedding_adapter,
)


def create_production_embedding_provider(
    settings: Settings,
    *,
    require_records: bool,
    provider: Any | None = None,
    provider_factory: Callable[[Settings], Any] | None = None,
    module_loader: Callable[[str], Any] | None = None,
) -> AsyncEmbeddingProviderAdapter:
    if (
        require_records
        and next(BrainRecordStore(settings.project_root).iter_records(), None) is None
    ):
        raise ValueError("production memory index requires normalized brain records")
    selected = settings.embedding_provider.strip().lower()
    if selected == "openai":
        if not settings.env_value("OPENAI_API_KEY"):
            raise ValueError(
                "production memory index requires OPENAI_API_KEY"
            )
        loader = module_loader or import_module
        try:
            openai_module = loader("openai")
        except ImportError as exc:
            raise ValueError(
                "production vector index rebuild requires the openai SDK"
            ) from exc
        if not hasattr(openai_module, "AsyncOpenAI"):
            raise ValueError(
                "production vector index rebuild requires an openai SDK "
                "exposing AsyncOpenAI"
            )
    if provider is None and selected in {"openai", "llm"}:
        factory = provider_factory or create_llm_provider
        provider = factory(settings)
    try:
        return configured_embedding_adapter(
            settings,
            production=True,
            llm_provider=provider,
        )
    except ProductionEmbeddingUnavailableError as exc:
        raise ValueError(str(exc)) from exc


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
    direct_method = getattr(provider, "embedding_method", None)
    if isinstance(direct_method, str) and direct_method.strip():
        return direct_method.strip()
    base_provider = getattr(provider, "provider", provider)
    direct_method = getattr(base_provider, "embedding_method", None)
    if isinstance(direct_method, str) and direct_method.strip():
        return direct_method.strip()
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
    return f"real_embedding:{settings.embedding_provider.strip().lower()}:{model}"
