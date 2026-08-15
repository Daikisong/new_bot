from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.retrieval import production_embedding
from news_scalping_lab.retrieval.production_embedding import (
    LOCAL_EMBEDDING_ALLOW_PATTERNS,
    LOCAL_EMBEDDING_FAST_HASH_FILES,
    LOCAL_EMBEDDING_IDENTITY_FILE,
    LOCAL_EMBEDDING_IGNORE_PATTERNS,
    LOCAL_EMBEDDING_MODEL_MANIFEST_FILE,
    LocalEmbeddingModelManifest,
    ProductionEmbeddingUnavailableError,
    load_local_production_embedding,
    prepare_local_production_embedding,
    selected_artifact_root_sha256,
    verify_local_production_embedding,
)
from news_scalping_lab.utils import read_json


class _SentenceModel:
    def get_sentence_embedding_dimension(self) -> int:
        return 4

    def encode(self, sentences: list[str], **kwargs: Any) -> np.ndarray:
        del kwargs
        vector = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        vector /= np.linalg.norm(vector)
        return np.stack([vector for _ in sentences])


def _snapshot(root: Path) -> Path:
    model_root = root / "model"
    payloads = {
        "1_Pooling/config.json": b'{"pooling_mode_mean_tokens":true}',
        "README.md": b"model card",
        "config.json": b'{"hidden_size":4}',
        "config_sentence_transformers.json": b'{"prompts":{}}',
        "model.safetensors": b"native-safe-weights",
        "modules.json": b"[]",
        "sentence_bert_config.json": b'{"max_seq_length":128}',
        "sentencepiece.bpe.model": b"sentencepiece",
        "special_tokens_map.json": b'{"unk_token":"<unk>"}',
        "tokenizer.json": b'{"version":"1.0"}',
        "tokenizer_config.json": b'{"tokenizer_class":"PreTrainedTokenizerFast"}',
        "unigram.json": b"{}",
        "onnx/model.onnx": b"excluded-onnx",
        "openvino/openvino_model.bin": b"excluded-openvino",
        "pytorch_model.bin": b"duplicate-pytorch",
        "tf_model.h5": b"excluded-tensorflow",
    }
    for relative_path, payload in payloads.items():
        path = model_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    return model_root


def _prepare(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    loader_calls: list[dict[str, Any]] | None = None,
) -> tuple[Settings, dict[str, Any], list[dict[str, Any]]]:
    production_embedding._clear_local_embedding_caches_for_tests()
    model_root = _snapshot(tmp_path)
    download_calls: list[dict[str, Any]] = []
    calls = loader_calls if loader_calls is not None else []

    def snapshot_download(**kwargs: Any) -> str:
        download_calls.append(kwargs)
        return str(model_root)

    siblings = [
        SimpleNamespace(
            rfilename=path.relative_to(model_root).as_posix(),
            size=path.stat().st_size,
        )
        for path in sorted(model_root.rglob("*"))
        if path.is_file()
    ]
    api = SimpleNamespace(
        model_info=lambda *args, **kwargs: SimpleNamespace(siblings=siblings)
    )
    hub = SimpleNamespace(snapshot_download=snapshot_download, HfApi=lambda: api)

    def loader(*args: Any, **kwargs: Any) -> _SentenceModel:
        calls.append({"args": args, "kwargs": kwargs})
        return _SentenceModel()

    modules = {
        "huggingface_hub": hub,
        "sentence_transformers": SimpleNamespace(SentenceTransformer=loader),
        "torch": SimpleNamespace(
            cuda=SimpleNamespace(is_available=lambda: False)
        ),
    }
    monkeypatch.setattr(
        production_embedding,
        "import_module",
        lambda name: modules[name],
    )
    settings = Settings(
        project_root=tmp_path,
        embedding_provider="local-production",
        local_embedding_model="fixture-model",
        local_embedding_revision="fixture-revision",
    )
    identity = prepare_local_production_embedding(settings)
    return settings, identity, download_calls


def test_local_embedding_download_uses_allow_patterns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, calls = _prepare(tmp_path, monkeypatch)

    assert calls[0]["allow_patterns"] == list(LOCAL_EMBEDDING_ALLOW_PATTERNS)
    assert calls[0]["ignore_patterns"] == list(LOCAL_EMBEDDING_IGNORE_PATTERNS)


def test_local_embedding_excludes_duplicate_distribution_formats(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, identity, _ = _prepare(tmp_path, monkeypatch)
    manifest = LocalEmbeddingModelManifest.model_validate(
        read_json(settings.path(LOCAL_EMBEDDING_MODEL_MANIFEST_FILE))
    )
    paths = {entry.relative_path for entry in manifest.selected_files}

    assert "model.safetensors" in paths
    assert "pytorch_model.bin" not in paths
    assert "tf_model.h5" not in paths
    assert not any(path.startswith(("onnx/", "openvino/")) for path in paths)
    assert identity["excluded_file_count"] == 4


def test_selected_snapshot_loads_with_sentence_transformer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = _prepare(tmp_path, monkeypatch)

    provider = load_local_production_embedding(settings)
    vectors = provider.embed_texts(["한국어", "English"])

    assert provider.dimensions == 4
    assert len(vectors) == 2


def test_embedding_manifest_contains_all_selected_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, identity, _ = _prepare(tmp_path, monkeypatch)
    manifest = LocalEmbeddingModelManifest.model_validate(
        read_json(settings.path(LOCAL_EMBEDDING_MODEL_MANIFEST_FILE))
    )

    assert manifest.selected_file_count == len(LOCAL_EMBEDDING_ALLOW_PATTERNS)
    assert manifest.selected_total_bytes == sum(
        entry.size_bytes for entry in manifest.selected_files
    )
    assert identity["embedding_artifact_sha256"] == manifest.artifact_root_sha256
    assert identity["selected_download_bytes"] == manifest.selected_total_bytes


def test_embedding_manifest_root_hash_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = _prepare(tmp_path, monkeypatch)
    manifest = LocalEmbeddingModelManifest.model_validate(
        read_json(settings.path(LOCAL_EMBEDDING_MODEL_MANIFEST_FILE))
    )

    assert selected_artifact_root_sha256(manifest.selected_files) == (
        selected_artifact_root_sha256(list(reversed(manifest.selected_files)))
    )


def test_embedding_manifest_detects_tampered_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = _prepare(tmp_path, monkeypatch)
    identity = read_json(settings.path(LOCAL_EMBEDDING_IDENTITY_FILE))
    model_path = Path(identity["model_path"])
    weight_path = model_path / "model.safetensors"
    weight_path.write_bytes(b"x" * weight_path.stat().st_size)
    production_embedding._clear_local_embedding_caches_for_tests()

    with pytest.raises(
        ProductionEmbeddingUnavailableError,
        match="artifact hash drift",
    ):
        verify_local_production_embedding(settings, deep=True)


def test_fast_runtime_verification_does_not_hash_entire_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = _prepare(tmp_path, monkeypatch)
    production_embedding._clear_local_embedding_caches_for_tests()
    real_hash = production_embedding.file_sha256
    hashed: list[str] = []

    def recording_hash(path: Path) -> str:
        hashed.append(path.as_posix())
        return real_hash(path)

    monkeypatch.setattr(production_embedding, "file_sha256", recording_hash)
    result = verify_local_production_embedding(settings, deep=False)

    assert result["hashed_file_count"] == len(LOCAL_EMBEDDING_FAST_HASH_FILES)
    assert not any("onnx/" in path or "openvino/" in path for path in hashed)
    assert not any(path.endswith("README.md") for path in hashed)
    assert any(path.endswith("model.safetensors") for path in hashed)


def test_deep_verification_hashes_all_selected_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = _prepare(tmp_path, monkeypatch)
    production_embedding._clear_local_embedding_caches_for_tests()
    real_hash = production_embedding.file_sha256
    hashed: list[str] = []

    def recording_hash(path: Path) -> str:
        hashed.append(path.as_posix())
        return real_hash(path)

    monkeypatch.setattr(production_embedding, "file_sha256", recording_hash)
    result = verify_local_production_embedding(settings, deep=True)

    assert result["hashed_file_count"] == len(LOCAL_EMBEDDING_ALLOW_PATTERNS)
    assert any(path.endswith("README.md") for path in hashed)
    assert any(path.endswith("sentencepiece.bpe.model") for path in hashed)


def test_runtime_reuses_model_within_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader_calls: list[dict[str, Any]] = []
    settings, _, _ = _prepare(
        tmp_path,
        monkeypatch,
        loader_calls=loader_calls,
    )

    first = load_local_production_embedding(settings)
    second = load_local_production_embedding(settings)

    assert first is second
    assert len(loader_calls) == 1


def test_runtime_identity_change_invalidates_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loader_calls: list[dict[str, Any]] = []
    settings, _, _ = _prepare(
        tmp_path,
        monkeypatch,
        loader_calls=loader_calls,
    )
    first = load_local_production_embedding(settings)
    settings.local_embedding_revision = "fixture-revision-2"
    prepare_local_production_embedding(settings)
    second = load_local_production_embedding(settings)

    assert first is not second
    assert len(loader_calls) == 2


def test_fast_verification_failure_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, _, _ = _prepare(tmp_path, monkeypatch)
    identity = read_json(settings.path(LOCAL_EMBEDDING_IDENTITY_FILE))
    (Path(identity["model_path"]) / "tokenizer.json").unlink()
    production_embedding._clear_local_embedding_caches_for_tests()

    with pytest.raises(
        ProductionEmbeddingUnavailableError,
        match="file is missing",
    ):
        load_local_production_embedding(settings)
