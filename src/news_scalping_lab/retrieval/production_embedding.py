"""Pinned, no-key semantic embedding runtime for production retrieval."""

from __future__ import annotations

import math
import threading
from datetime import datetime
from fnmatch import fnmatchcase
from importlib import import_module
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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

LOCAL_EMBEDDING_IDENTITY_FILE = Path("diagnostics/local_embedding_identity.json")
LOCAL_EMBEDDING_MODEL_MANIFEST_FILE = Path("memory/embedding_model_manifest.json")
LOCAL_EMBEDDING_PROVIDER = "local-production"
LOCAL_EMBEDDING_NORMALIZATION = "l2"

# Pinned to the repository layout at LOCAL_EMBEDDING_REVISION. model.safetensors
# is the complete PyTorch weight payload loaded by SentenceTransformer here.
# The ignored PyTorch .bin is a duplicate serialization; TF, ONNX, and OpenVINO
# files target other runtimes. Excluding them reduces delivery size, not model
# weights, tokenizer behavior, dimensions, or embedding quality.
LOCAL_EMBEDDING_ALLOW_PATTERNS = (
    "1_Pooling/config.json",
    "README.md",
    "config.json",
    "config_sentence_transformers.json",
    "model.safetensors",
    "modules.json",
    "sentence_bert_config.json",
    "sentencepiece.bpe.model",
    "special_tokens_map.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "unigram.json",
)
LOCAL_EMBEDDING_IGNORE_PATTERNS = (
    "onnx/**",
    "openvino/**",
    "*.onnx",
    "pytorch_model.bin",
    "tf_model.h5",
)
LOCAL_EMBEDDING_REQUIRED_FILES = frozenset(
    {
        "1_Pooling/config.json",
        "config.json",
        "config_sentence_transformers.json",
        "model.safetensors",
        "modules.json",
        "sentence_bert_config.json",
        "special_tokens_map.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)
LOCAL_EMBEDDING_FAST_HASH_FILES = frozenset(
    {
        "1_Pooling/config.json",
        "config.json",
        "model.safetensors",
        "modules.json",
        "tokenizer.json",
        "tokenizer_config.json",
    }
)
_MODEL_CACHE_LIMIT = 4
_FAST_VERIFICATION_CACHE_LIMIT = 16
_MODEL_CACHE: dict[tuple[Any, ...], LocalProductionEmbeddingProvider] = {}
_FAST_VERIFICATION_CACHE: dict[tuple[Any, ...], bool] = {}
_CACHE_LOCK = threading.RLock()


class ProductionEmbeddingUnavailableError(RuntimeError):
    """Raised before prediction when a production semantic provider is unusable."""


class SentenceModel(Protocol):
    def encode(self, sentences: list[str], **kwargs: Any) -> Any:
        """Return one dense vector per sentence."""

    def get_sentence_embedding_dimension(self) -> int | None:
        """Return the fixed embedding dimension."""


class LocalEmbeddingSelectedFile(BaseModel):
    """One immutable file selected from the pinned model repository."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relative_path: str
    size_bytes: int = Field(ge=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        candidate = Path(normalized)
        if not normalized or candidate.is_absolute() or ".." in candidate.parts or normalized.startswith("/"):
            raise ValueError("embedding model file path must be canonical and relative")
        return normalized


class LocalEmbeddingModelManifest(BaseModel):
    """Content-addressed projection of the selected SentenceTransformer files."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["nslab.local_embedding_model_manifest.v1"] = "nslab.local_embedding_model_manifest.v1"
    provider: Literal["local-production"] = "local-production"
    model: str
    revision: str
    dimension: int = Field(ge=1)
    normalization: Literal["l2"] = "l2"
    model_path: str
    allow_patterns: list[str]
    ignore_patterns: list[str]
    selected_files: list[LocalEmbeddingSelectedFile]
    selected_file_count: int = Field(ge=1)
    selected_total_bytes: int = Field(ge=1)
    full_repository_size_if_known: int | None = Field(default=None, ge=1)
    excluded_file_count: int | None = Field(default=None, ge=0)
    artifact_root_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: datetime

    @model_validator(mode="after")
    def validate_projection(self) -> LocalEmbeddingModelManifest:
        paths = [entry.relative_path for entry in self.selected_files]
        if paths != sorted(paths) or len(paths) != len(set(paths)):
            raise ValueError("embedding model files must be unique and sorted")
        if self.selected_file_count != len(self.selected_files):
            raise ValueError("embedding selected file count mismatch")
        if self.selected_total_bytes != sum(entry.size_bytes for entry in self.selected_files):
            raise ValueError("embedding selected byte count mismatch")
        if not LOCAL_EMBEDDING_REQUIRED_FILES.issubset(paths):
            raise ValueError("embedding model manifest is missing a required file")
        if self.artifact_root_sha256 != selected_artifact_root_sha256(self.selected_files):
            raise ValueError("embedding model manifest root mismatch")
        return self


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
        fast_verification_seconds: float | None = None,
        model_load_seconds: float | None = None,
    ) -> None:
        self._model = model
        self.embedding_model = model_name
        self.embedding_revision = revision
        self.embedding_artifact_sha256 = artifact_sha256
        self.model_path = model_path
        self.device = device
        self.batch_size = max(1, batch_size)
        self.fast_verification_seconds = fast_verification_seconds
        self.model_load_seconds = model_load_seconds
        self.normalization = LOCAL_EMBEDDING_NORMALIZATION
        # These monotonic counters let paired shadow runs measure real model
        # work without changing query results or relying on provider logs.
        self.embedding_query_count = 0
        self.embedding_text_count = 0
        self.embedding_input_char_count = 0
        dimension_reader = getattr(model, "get_embedding_dimension", None)
        dimensions = dimension_reader() if callable(dimension_reader) else model.get_sentence_embedding_dimension()
        if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 1:
            raise ProductionEmbeddingUnavailableError("local embedding model did not report a fixed dimension")
        self.dimensions = dimensions
        self.embedding_method = f"local_production:{model_name}@{revision}:sha256:{artifact_sha256}:l2"

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        del purpose
        return self.embed_texts(texts)

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        self.embedding_query_count += 1
        self.embedding_text_count += len(texts)
        self.embedding_input_char_count += sum(len(text) for text in texts)
        output = self._model.encode(
            texts,
            batch_size=self.batch_size,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        values = output.tolist() if hasattr(output, "tolist") else output
        if not isinstance(values, list):
            raise ProductionEmbeddingUnavailableError("local embedding model returned an invalid batch")
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
    repository = _repository_file_projection(
        hub,
        model=settings.local_embedding_model,
        revision=settings.local_embedding_revision,
    )
    model_path = Path(
        hub.snapshot_download(
            repo_id=settings.local_embedding_model,
            revision=settings.local_embedding_revision,
            cache_dir=cache_root,
            allow_patterns=list(LOCAL_EMBEDDING_ALLOW_PATTERNS),
            ignore_patterns=list(LOCAL_EMBEDDING_IGNORE_PATTERNS),
        )
    ).resolve()
    deep_started = perf_counter()
    selected_files = selected_model_files(model_path)
    deep_verification_seconds = perf_counter() - deep_started
    artifact_sha256 = selected_artifact_root_sha256(selected_files)
    manifest = LocalEmbeddingModelManifest(
        model=settings.local_embedding_model,
        revision=settings.local_embedding_revision,
        dimension=_configured_model_dimension(model_path),
        model_path=model_path.as_posix(),
        allow_patterns=list(LOCAL_EMBEDDING_ALLOW_PATTERNS),
        ignore_patterns=list(LOCAL_EMBEDDING_IGNORE_PATTERNS),
        selected_files=selected_files,
        selected_file_count=len(selected_files),
        selected_total_bytes=sum(entry.size_bytes for entry in selected_files),
        full_repository_size_if_known=repository["total_bytes"],
        excluded_file_count=repository["excluded_file_count"],
        artifact_root_sha256=artifact_sha256,
        created_at=now_kst(),
    )
    manifest_path = settings.path(LOCAL_EMBEDDING_MODEL_MANIFEST_FILE)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(manifest_path, manifest.model_dump(mode="json"))
    identity = {
        "schema_version": "nslab.local_embedding_identity.v2",
        "embedding_provider": LOCAL_EMBEDDING_PROVIDER,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "embedding_artifact_sha256": artifact_sha256,
        "embedding_dimensions": manifest.dimension,
        "normalization": manifest.normalization,
        "device": _preferred_device(),
        "model_path": model_path.as_posix(),
        "embedding_model_manifest_path": (LOCAL_EMBEDDING_MODEL_MANIFEST_FILE.as_posix()),
        "embedding_model_manifest_sha256": file_sha256(manifest_path),
        "full_repository_size_if_known": repository["total_bytes"],
        "selected_download_bytes": manifest.selected_total_bytes,
        "excluded_file_count": repository["excluded_file_count"],
        "deep_verification_seconds": deep_verification_seconds,
        "fast_verification_seconds": None,
        "model_load_seconds": None,
        "peak_memory_if_measured": None,
        "prepared_at": now_kst().isoformat(),
    }
    identity_path = settings.path(LOCAL_EMBEDDING_IDENTITY_FILE)
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    write_json(identity_path, identity)
    provider = load_local_production_embedding(
        settings,
        model_loader=sentence_transformers.SentenceTransformer,
    )
    identity["embedding_dimensions"] = provider.dimensions
    identity["device"] = provider.device
    identity["fast_verification_seconds"] = provider.fast_verification_seconds
    identity["model_load_seconds"] = provider.model_load_seconds
    write_json(identity_path, identity)
    return identity


def load_local_production_embedding(
    settings: Settings,
    *,
    model_loader: Any | None = None,
) -> LocalProductionEmbeddingProvider:
    verification = verify_local_production_embedding(settings, deep=False)
    identity = verification["identity"]
    manifest = verification["manifest"]
    model_path = Path(manifest.model_path).resolve()
    artifact_sha256 = manifest.artifact_root_sha256
    if model_loader is None:
        try:
            sentence_transformers = import_module("sentence_transformers")
        except ImportError as exc:
            raise ProductionEmbeddingUnavailableError(
                "install the production embedding extra: pip install .[production]"
            ) from exc
        model_loader = sentence_transformers.SentenceTransformer
    device = _preferred_device()
    # Content identity plus every selected file's size/mtime binds cache reuse.
    # A replaced model, manifest, loader, device, or touched selected file gets
    # a new key instead of silently reusing an already loaded provider.
    cache_key = (
        settings.local_embedding_model,
        settings.local_embedding_revision,
        device,
        model_path.as_posix(),
        artifact_sha256,
        verification["manifest_sha256"],
        verification["stat_signature"],
        id(model_loader),
    )
    with _CACHE_LOCK:
        cached = _MODEL_CACHE.get(cache_key)
        if cached is not None:
            return cached
        load_started = perf_counter()
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
            fast_verification_seconds=verification["verification_seconds"],
            model_load_seconds=perf_counter() - load_started,
        )
        if identity.get("embedding_dimensions") != provider.dimensions or manifest.dimension != provider.dimensions:
            raise ProductionEmbeddingUnavailableError("local embedding model dimension drift")
        provider.embed_texts(["한국 상장사 공급계약", "English listed-company supply contract"])
        _bounded_cache_put(_MODEL_CACHE, cache_key, provider, _MODEL_CACHE_LIMIT)
        return provider


def verify_local_production_embedding(
    settings: Settings,
    *,
    deep: bool,
) -> dict[str, Any]:
    """Verify the sealed model snapshot without loading SentenceTransformer."""

    identity_path = settings.path(LOCAL_EMBEDDING_IDENTITY_FILE)
    manifest_path = settings.path(LOCAL_EMBEDDING_MODEL_MANIFEST_FILE)
    try:
        identity = read_json(identity_path)
        manifest = LocalEmbeddingModelManifest.model_validate(read_json(manifest_path))
    except (OSError, ValueError) as exc:
        raise ProductionEmbeddingUnavailableError("local production embedding is not prepared") from exc
    if not isinstance(identity, dict):
        raise ProductionEmbeddingUnavailableError("local embedding identity receipt is invalid")
    expected = {
        "schema_version": "nslab.local_embedding_identity.v2",
        "embedding_provider": LOCAL_EMBEDDING_PROVIDER,
        "embedding_model": settings.local_embedding_model,
        "embedding_revision": settings.local_embedding_revision,
        "normalization": LOCAL_EMBEDDING_NORMALIZATION,
        "embedding_model_manifest_path": (LOCAL_EMBEDDING_MODEL_MANIFEST_FILE.as_posix()),
    }
    if any(identity.get(key) != value for key, value in expected.items()):
        raise ProductionEmbeddingUnavailableError(
            "local embedding identity differs from configured production settings"
        )
    try:
        manifest_sha256 = file_sha256(manifest_path)
    except OSError as exc:
        raise ProductionEmbeddingUnavailableError("local embedding manifest is unreadable") from exc
    if (
        manifest.provider != LOCAL_EMBEDDING_PROVIDER
        or manifest.model != settings.local_embedding_model
        or manifest.revision != settings.local_embedding_revision
        or manifest.normalization != LOCAL_EMBEDDING_NORMALIZATION
        or identity.get("model_path") != manifest.model_path
        or identity.get("embedding_dimensions") != manifest.dimension
        or identity.get("embedding_artifact_sha256") != manifest.artifact_root_sha256
        or identity.get("embedding_model_manifest_sha256") != manifest_sha256
    ):
        raise ProductionEmbeddingUnavailableError("local embedding manifest identity mismatch")
    model_path = Path(manifest.model_path).resolve()
    if not model_path.is_dir():
        raise ProductionEmbeddingUnavailableError("local embedding model directory is missing")
    started = perf_counter()
    # The cheap path always stats every selected file. This proves existence and
    # size and also creates the cache-invalidating signature without reading the
    # roughly 500 MB model payload on every CLI process start.
    stat_signature = tuple(
        _verify_selected_file(
            model_path,
            entry,
            hash_content=False,
        )
        for entry in manifest.selected_files
    )
    verification_key = (
        manifest_sha256,
        stat_signature,
        tuple(sorted(LOCAL_EMBEDDING_FAST_HASH_FILES)),
    )
    hashed_file_count = 0
    if deep:
        # Bootstrap, release finalization, and deep doctor hash the full selected
        # snapshot. Runtime fast checks hash only the weight/config/tokenizer
        # trust anchors below; alternate distribution files were never selected.
        for entry in manifest.selected_files:
            _verify_selected_file(model_path, entry, hash_content=True)
        hashed_file_count = manifest.selected_file_count
    else:
        with _CACHE_LOCK:
            already_verified = _FAST_VERIFICATION_CACHE.get(verification_key) is True
        if not already_verified:
            for entry in manifest.selected_files:
                if entry.relative_path in LOCAL_EMBEDDING_FAST_HASH_FILES:
                    _verify_selected_file(model_path, entry, hash_content=True)
                    hashed_file_count += 1
            with _CACHE_LOCK:
                _bounded_cache_put(
                    _FAST_VERIFICATION_CACHE,
                    verification_key,
                    True,
                    _FAST_VERIFICATION_CACHE_LIMIT,
                )
    return {
        "schema_version": "nslab.local_embedding_verification.v1",
        "passed": True,
        "deep": deep,
        "identity": identity,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "manifest_sha256": manifest_sha256,
        "artifact_root_sha256": manifest.artifact_root_sha256,
        "selected_file_count": manifest.selected_file_count,
        "selected_total_bytes": manifest.selected_total_bytes,
        "hashed_file_count": hashed_file_count,
        "stat_signature": stat_signature,
        "verification_seconds": perf_counter() - started,
    }


def local_embedding_verification_status(
    settings: Settings,
    *,
    deep: bool,
) -> dict[str, Any]:
    """Return a secret-free doctor/report projection for the model snapshot."""

    try:
        verified = verify_local_production_embedding(settings, deep=deep)
    except ProductionEmbeddingUnavailableError as exc:
        return {
            "schema_version": "nslab.local_embedding_verification_status.v1",
            "passed": False,
            "deep": deep,
            "error": str(exc),
        }
    manifest = verified["manifest"]
    return {
        "schema_version": "nslab.local_embedding_verification_status.v1",
        "passed": True,
        "deep": deep,
        "manifest_path": LOCAL_EMBEDDING_MODEL_MANIFEST_FILE.as_posix(),
        "artifact_root_sha256": manifest.artifact_root_sha256,
        "selected_file_count": manifest.selected_file_count,
        "selected_total_bytes": manifest.selected_total_bytes,
        "full_repository_size_if_known": manifest.full_repository_size_if_known,
        "excluded_file_count": manifest.excluded_file_count,
        "hashed_file_count": verified["hashed_file_count"],
        "verification_seconds": verified["verification_seconds"],
    }


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
            EmbeddingFallbackPolicy.parse(settings.event_cluster_fallback_policy) is EmbeddingFallbackPolicy.FAIL_CLOSED
        ):
            raise ProductionEmbeddingUnavailableError("production cannot use deterministic hash embeddings")
        return llm_provider or DeterministicHashEmbeddingProvider()
    if selected in {"openai", "llm"} and llm_provider is not None:
        return llm_provider
    raise ProductionEmbeddingUnavailableError(f"unsupported embedding provider: {settings.embedding_provider}")


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
            raise ProductionEmbeddingUnavailableError("embedding provider identity is unavailable")
        embedding_method = f"real_embedding:{settings.embedding_provider}:{model}"
    return AsyncEmbeddingProviderAdapter(
        provider,
        embedding_method=embedding_method,
        production_capability_attested=production,
    )


def directory_artifact_sha256(root: Path) -> str:
    if not root.is_dir():
        raise ProductionEmbeddingUnavailableError("local embedding model directory is missing")
    entries = {
        path.relative_to(root).as_posix(): file_sha256(path) for path in sorted(root.rglob("*")) if path.is_file()
    }
    if not entries:
        raise ProductionEmbeddingUnavailableError("local embedding model directory is empty")
    return sha256_text(canonical_json(entries))


def selected_model_files(root: Path) -> list[LocalEmbeddingSelectedFile]:
    """Hash only the files explicitly selected for the pinned runtime."""

    if not root.is_dir():
        raise ProductionEmbeddingUnavailableError("local embedding model directory is missing")
    selected: list[LocalEmbeddingSelectedFile] = []
    for path in sorted(
        (candidate for candidate in root.rglob("*") if candidate.is_file()),
        key=lambda candidate: candidate.relative_to(root).as_posix(),
    ):
        relative = path.relative_to(root).as_posix()
        if not _matches_patterns(relative, LOCAL_EMBEDDING_ALLOW_PATTERNS):
            continue
        stat = path.stat()
        selected.append(
            LocalEmbeddingSelectedFile(
                relative_path=relative,
                size_bytes=stat.st_size,
                sha256=file_sha256(path),
            )
        )
    paths = {entry.relative_path for entry in selected}
    if not LOCAL_EMBEDDING_REQUIRED_FILES.issubset(paths):
        missing = sorted(LOCAL_EMBEDDING_REQUIRED_FILES - paths)
        raise ProductionEmbeddingUnavailableError(
            "local embedding snapshot is missing required files: " + ", ".join(missing)
        )
    if not selected:
        raise ProductionEmbeddingUnavailableError("local embedding selected snapshot is empty")
    return selected


def selected_artifact_root_sha256(
    selected_files: list[LocalEmbeddingSelectedFile],
) -> str:
    # Root the logical file projection, not absolute cache paths, directory
    # metadata, or download timestamps, so clean installs share one identity.
    projection = [
        entry.model_dump(mode="json") for entry in sorted(selected_files, key=lambda item: item.relative_path)
    ]
    return sha256_text(canonical_json(projection))


def _configured_model_dimension(model_path: Path) -> int:
    try:
        config = read_json(model_path / "config.json")
    except (OSError, ValueError) as exc:
        raise ProductionEmbeddingUnavailableError("local embedding config is unreadable") from exc
    dimension = config.get("hidden_size") if isinstance(config, dict) else None
    if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension < 1:
        raise ProductionEmbeddingUnavailableError("local embedding config has no fixed hidden size")
    return dimension


def _verify_selected_file(
    model_path: Path,
    entry: LocalEmbeddingSelectedFile,
    *,
    hash_content: bool,
) -> tuple[str, int, int]:
    path = model_path / Path(entry.relative_path)
    try:
        is_file = path.is_file()
        stat = path.stat() if is_file else None
    except OSError as exc:
        raise ProductionEmbeddingUnavailableError(
            f"local embedding model file is unreadable: {entry.relative_path}"
        ) from exc
    if not is_file or stat is None:
        raise ProductionEmbeddingUnavailableError(f"local embedding model file is missing: {entry.relative_path}")
    if stat.st_size != entry.size_bytes:
        raise ProductionEmbeddingUnavailableError(f"local embedding model file size drift: {entry.relative_path}")
    if hash_content:
        try:
            actual_sha256 = file_sha256(path)
        except OSError as exc:
            raise ProductionEmbeddingUnavailableError(
                f"local embedding model file is unreadable: {entry.relative_path}"
            ) from exc
        if actual_sha256 != entry.sha256:
            raise ProductionEmbeddingUnavailableError(
                f"local embedding model artifact hash drift: {entry.relative_path}"
            )
    return (entry.relative_path, stat.st_size, stat.st_mtime_ns)


def _repository_file_projection(
    hub: Any,
    *,
    model: str,
    revision: str,
) -> dict[str, int | None]:
    # Hub inventory is diagnostics only. Missing network metadata becomes null;
    # runtime safety continues to depend on the local sealed manifest and hashes.
    try:
        info = hub.HfApi().model_info(
            model,
            revision=revision,
            files_metadata=True,
        )
        siblings = list(getattr(info, "siblings", []))
        names = [str(item.rfilename) for item in siblings if isinstance(getattr(item, "rfilename", None), str)]
        sizes: list[int] = []
        sizes_complete = bool(siblings)
        for item in siblings:
            size = getattr(item, "size", None)
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                sizes_complete = False
                continue
            sizes.append(size)
        total_bytes = sum(sizes) if sizes_complete and len(sizes) == len(siblings) else None
        excluded = sum(1 for name in names if not _matches_patterns(name, LOCAL_EMBEDDING_ALLOW_PATTERNS))
        return {
            "total_bytes": total_bytes,
            "excluded_file_count": excluded,
        }
    except Exception:
        return {"total_bytes": None, "excluded_file_count": None}


def _matches_patterns(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(path, pattern) for pattern in patterns)


def _bounded_cache_put(
    cache: dict[Any, Any],
    key: Any,
    value: Any,
    limit: int,
) -> None:
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))


def _clear_local_embedding_caches_for_tests() -> None:
    with _CACHE_LOCK:
        _MODEL_CACHE.clear()
        _FAST_VERIFICATION_CACHE.clear()


def embedding_identity(provider: Any) -> dict[str, Any]:
    actual = getattr(provider, "provider", provider)
    return {
        "embedding_provider": (
            LOCAL_EMBEDDING_PROVIDER if isinstance(actual, LocalProductionEmbeddingProvider) else type(actual).__name__
        ),
        "embedding_model": getattr(actual, "embedding_model", None),
        "embedding_revision": getattr(actual, "embedding_revision", None),
        "embedding_artifact_sha256": getattr(actual, "embedding_artifact_sha256", None),
        "embedding_dimensions": getattr(actual, "dimensions", 0),
        "normalization": getattr(actual, "normalization", None),
        "device": getattr(actual, "device", None),
        "embedding_method": getattr(provider, "embedding_method", None) or getattr(actual, "embedding_method", None),
    }


def _validate_dense_vectors(
    vectors: list[list[Any]],
    *,
    expected_count: int,
    expected_dimensions: int,
) -> None:
    if len(vectors) != expected_count:
        raise ProductionEmbeddingUnavailableError("embedding provider returned the wrong vector count")
    for vector in vectors:
        if len(vector) != expected_dimensions:
            raise ProductionEmbeddingUnavailableError("embedding provider dimension mismatch")
        if any(
            isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(float(value))
            for value in vector
        ):
            raise ProductionEmbeddingUnavailableError("embedding vectors must contain finite numeric values")
        magnitude = math.sqrt(sum(float(value) ** 2 for value in vector))
        if not 0.999 <= magnitude <= 1.001:
            raise ProductionEmbeddingUnavailableError("local embedding vectors must be L2 normalized")


def _preferred_device() -> str:
    try:
        torch = import_module("torch")
    except ImportError:
        return "cpu"
    cuda = getattr(torch, "cuda", None)
    return "cuda" if cuda is not None and cuda.is_available() else "cpu"
