"""Pinned, no-key semantic embedding runtime for production retrieval."""

from __future__ import annotations

import math
from importlib import import_module
from pathlib import Path
from typing import Any, Protocol

from news_scalping_lab.config import Settings
from news_scalping_lab.llm.codex_oauth_provider import (
    probe_codex_embedding_capability,
)
from news_scalping_lab.policies import EmbeddingFallbackPolicy
from news_scalping_lab.retrieval.embedding import (
    AsyncEmbeddingProviderAdapter,
    DeterministicHashEmbeddingProvider,
)
from news_scalping_lab.utils import (
    canonical_json,
    file_sha256,
    now_kst,
    read_json,
    sha256_text,
    write_json,
)

LOCAL_EMBEDDING_IDENTITY_FILE = Path(
    "diagnostics/local_embedding_identity.json"
)
LOCAL_EMBEDDING_PROVIDER = "local-production"
LOCAL_EMBEDDING_NORMALIZATION = "l2"


class ProductionEmbeddingUnavailableError(RuntimeError):
    """Raised before prediction when a production semantic provider is unusable."""


class SentenceModel(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any:
        """Return one dense vector per sentence."""

    def get_sentence_embedding_dimension(self) -> int | None:
        """Return the fixed embedding dimension."""


class LocalProductionEmbeddingProvider:
    """Sentence-transformers model loaded only from a verified local snapshot."""

    production_capability_attested = True

    def __init__(
        self,
        *,
        model: SentenceModel,
        model_name: str,
        revision: str,
        artifact_sha256: str,
        model_path: Path,
        device: str,
        batch_size: int = 64,
    ) -> None:
        self._model = model
        self.embedding_model = model_name
        self.embedding_revision = revision
        self.embedding_artifact_sha256 = artifact_sha256
        self.model_path = model_path
        self.device = device
        self.batch_size = max(1, batch_size)
        self.normalization = LOCAL_EMBEDDING_NORMALIZATION
        dimension_reader = getattr(model, "get_embedding_dimension", None)
        dimensions = (
            dimension_reader()
            if callable(dimension_reader)
            else model.get_sentence_embedding_dimension()
        )
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1:
            raise ProductionEmbeddingUnavailableError(
                "local embedding model did not report a fixed dimension"
            )
        self.dimensions = dimensions
        self.embedding_method = (
            f"local_production:{model_name}@{revision}:"
            f"sha256:{artifact_sha256}:l2"
        )

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        del purpose
        return self.embed_texts(texts)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        output = self._model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        values = output.tolist() if hasattr(output, "tolist") else output
        if not isinstance(values, list):
            raise ProductionEmbeddingUnavailableError(
                "local embedding model returned an invalid batch"
            )
        vectors = [list(vector) for vector in values]
        _validate_dense_vectors(
            vectors,
            expected_count=len(texts),
            expected_dimensions=self.dimensions,
        )
        return [[float(value) for value in vector] for vector in vectors]


def prepare_local_production_embedding(settings: Settings) -> dict[str, Any]:
    try:
        hub = import_module("huggingface_hub")
        sentence_transformers = import_module("sentence_transformers")
    except ImportError as exc:
        raise ProductionEmbeddingUnavailableError(
            "install the production embedding extra: pip install .[production]"
        ) from exc
    cache_root = settings.path(settings.local_embedding_cache_path)
    cache_root.mkdir(parents=True, exist_ok=True)
    model_path = Path(
        hub.snapshot_download(
            repo_id=settings.local_embedding_model,
            revision=settings.local_embedding_revision,
            cache_dir=cache_root,
        )
    ).resolve()
    artifact_sha256 = directory_artifact_sha256(model_path)
    device = _preferred_device()
    model = sentence_transformers.SentenceTransformer(
        str(model_path),
        device=device,
        local_files_only=True,
    )
    provider = LocalProductionEmbeddingProvider(
        model=model,
        model_name=settings.local_embedding_model,
        revision=settings.local_embedding_revision,
        artifact_sha256=artifact_sha256,
        model_path=model_path,
        device=device,
    )
    provider.embed_texts(["한국어 상장사 공급계약", "English listed-company supply contract"])
    identity = {
        "schema_version": "nslab.local_embedding_identity.v1",
        "embedding_provider": LOCAL_EMBEDDING_PROVIDER,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "embedding_artifact_sha256": artifact_sha256,
        "embedding_dimensions": provider.dimensions,
        "normalization": provider.normalization,
        "device": device,
        "model_path": model_path.as_posix(),
        "prepared_at": now_kst().isoformat(),
    }
    identity_path = settings.path(LOCAL_EMBEDDING_IDENTITY_FILE)
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(identity_path, identity)
    return identity


def load_local_production_embedding(
    settings: Settings,
    *,
    model_loader: Any | None = None,
) -> LocalProductionEmbeddingProvider:
    identity_path = settings.path(LOCAL_EMBEDDING_IDENTITY_FILE)
    try:
        identity = read_json(identity_path)
    except (OSError, ValueError) as exc:
        raise ProductionEmbeddingUnavailableError(
            "local production embedding is not prepared"
        ) from exc
    if not isinstance(identity, dict):
        raise ProductionEmbeddingUnavailableError(
            "local embedding identity receipt is invalid"
        )
    expected = {
        "embedding_provider": LOCAL_EMBEDDING_PROVIDER,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "normalization": LOCAL_EMBEDDING_NORMALIZATION,
    }
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ProductionEmbeddingUnavailableError(
            "local embedding identity differs from configured production settings"
        )
    model_path_value = identity.get("model_path")
    artifact_sha256 = identity.get("embedding_artifact_sha256")
    if not isinstance(model_path_value, str) or not isinstance(
        artifact_sha256, str
    ):
        raise ProductionEmbeddingUnavailableError(
            "local embedding identity is incomplete"
        )
    model_path = Path(model_path_value).resolve()
    if not model_path.is_dir() or directory_artifact_sha256(model_path) != artifact_sha256:
        raise ProductionEmbeddingUnavailableError(
            "local embedding model artifact hash drift"
        )
    if model_loader is None:
        try:
            sentence_transformers = import_module("sentence_transformers")
        except ImportError as exc:
            raise ProductionEmbeddingUnavailableError(
                "install the production embedding extra: pip install .[production]"
            ) from exc
        model_loader = sentence_transformers.SentenceTransformer
    device = _preferred_device()
    model = model_loader(
        str(model_path),
        device=device,
        local_files_only=True,
    )
    provider = LocalProductionEmbeddingProvider(
        model=model,
        model_name=settings.local_embedding_model,
        revision=settings.local_embedding_revision,
        artifact_sha256=artifact_sha256,
        model_path=model_path,
        device=device,
    )
    declared_dimensions = identity.get("embedding_dimensions")
    if declared_dimensions != provider.dimensions:
        raise ProductionEmbeddingUnavailableError(
            "local embedding model dimension drift"
        )
    return provider


def create_configured_embedding_provider(
    settings: Settings,
    *,
    production: bool,
    llm_provider: Any | None = None,
) -> Any:
    selected = settings.embedding_provider.strip().lower()
    if selected == "auto":
        probe = probe_codex_embedding_capability(settings.codex_command)
        selected = "codex-oauth" if probe["supported"] is True else LOCAL_EMBEDDING_PROVIDER
    if selected in {LOCAL_EMBEDDING_PROVIDER, "local_production"}:
        return load_local_production_embedding(settings)
    if selected in {"codex-oauth", "codex_oauth"}:
        probe = probe_codex_embedding_capability(settings.codex_command)
        if probe["supported"] is not True:
            raise ProductionEmbeddingUnavailableError(
                "Codex OAuth embedding was probed and is not officially supported"
            )
        raise ProductionEmbeddingUnavailableError(
            "Codex OAuth embedding support requires an implemented official adapter"
        )
    if selected in {"mock", "deterministic", "deterministic-hash"}:
        if production or (
            EmbeddingFallbackPolicy.parse(settings.event_cluster_fallback_policy)
            is EmbeddingFallbackPolicy.FAIL_CLOSED
        ):
            raise ProductionEmbeddingUnavailableError(
                "production cannot use deterministic hash embeddings"
            )
        return llm_provider or DeterministicHashEmbeddingProvider()
    if selected in {"openai", "llm"} and llm_provider is not None:
        return llm_provider
    raise ProductionEmbeddingUnavailableError(
        f"unsupported embedding provider: {settings.embedding_provider}"
    )


def configured_embedding_adapter(
    settings: Settings,
    *,
    production: bool,
    llm_provider: Any | None = None,
) -> AsyncEmbeddingProviderAdapter:
    provider = create_configured_embedding_provider(
        settings,
        production=production,
        llm_provider=llm_provider,
    )
    if isinstance(provider, DeterministicHashEmbeddingProvider):
        return AsyncEmbeddingProviderAdapter(
            provider,
            embedding_method=provider.embedding_method,
            production_capability_attested=False,
        )
    embedding_method = getattr(provider, "embedding_method", None)
    if not isinstance(embedding_method, str) or not embedding_method.strip():
        model = getattr(provider, "embedding_model", None)
        if not isinstance(model, str) or not model.strip():
            raise ProductionEmbeddingUnavailableError(
                "embedding provider identity is unavailable"
            )
        embedding_method = f"real_embedding:{settings.embedding_provider}:{model}"
    return AsyncEmbeddingProviderAdapter(
        provider,
        embedding_method=embedding_method,
        production_capability_attested=production,
    )


def directory_artifact_sha256(root: Path) -> str:
    if not root.is_dir():
        raise ProductionEmbeddingUnavailableError(
            "local embedding model directory is missing"
        )
    entries = {
        path.relative_to(root).as_posix(): file_sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
    if not entries:
        raise ProductionEmbeddingUnavailableError(
            "local embedding model directory is empty"
        )
    return sha256_text(canonical_json(entries))


def embedding_identity(provider: Any) -> dict[str, Any]:
    actual = getattr(provider, "provider", provider)
    return {
        "embedding_provider": (
            LOCAL_EMBEDDING_PROVIDER
            if isinstance(actual, LocalProductionEmbeddingProvider)
            else type(actual).__name__
        ),
        "embedding_model": getattr(actual, "embedding_model", None),
        "embedding_revision": getattr(actual, "embedding_revision", None),
        "embedding_artifact_sha256": getattr(
            actual, "embedding_artifact_sha256", None
        ),
        "embedding_dimensions": getattr(actual, "dimensions", 0),
        "normalization": getattr(actual, "normalization", None),
        "device": getattr(actual, "device", None),
        "embedding_method": getattr(provider, "embedding_method", None)
        or getattr(actual, "embedding_method", None),
    }


def _validate_dense_vectors(
    vectors: list[list[Any]],
    *,
    expected_count: int,
    expected_dimensions: int,
) -> None:
    if len(vectors) != expected_count:
        raise ProductionEmbeddingUnavailableError(
            "embedding provider returned the wrong vector count"
        )
    for vector in vectors:
        if len(vector) != expected_dimensions:
            raise ProductionEmbeddingUnavailableError(
                "embedding provider dimension mismatch"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise ProductionEmbeddingUnavailableError(
                "embedding vectors must contain finite numeric values"
            )
        magnitude = math.sqrt(sum(float(value) ** 2 for value in vector))
        if not 0.999 <= magnitude <= 1.001:
            raise ProductionEmbeddingUnavailableError(
                "local embedding vectors must be L2 normalized"
            )


def _preferred_device() -> str:
    try:
        torch = import_module("torch")
    except ImportError:
        return "cpu"
    cuda = getattr(torch, "cuda", None)
    return "cuda" if cuda is not None and cuda.is_available() else "cpu"
