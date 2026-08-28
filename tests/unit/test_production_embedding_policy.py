from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import NewsItem
from news_scalping_lab.diagnostics import (
    build_doctor_report,
    production_readiness_report,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.inference.event_clustering import cluster_news_events
from news_scalping_lab.policies import EmbeddingFallbackPolicy
from news_scalping_lab.retrieval.production_embedding import (
    LOCAL_EMBEDDING_ALLOW_PATTERNS,
    LOCAL_EMBEDDING_IDENTITY_FILE,
    LOCAL_EMBEDDING_MODEL_MANIFEST_FILE,
    LocalEmbeddingModelManifest,
    LocalProductionEmbeddingProvider,
    ProductionEmbeddingUnavailableError,
    load_local_production_embedding,
    selected_artifact_root_sha256,
    selected_model_files,
)
from news_scalping_lab.utils import KST, file_sha256, now_kst, write_json


class _SentenceModel:
    def __init__(self, dimensions: int = 4) -> None:
        self.dimensions = dimensions

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimensions

    def encode(self, sentences: list[str], **kwargs: Any) -> np.ndarray:
        del kwargs
        vector = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
        vector /= np.linalg.norm(vector)
        return np.stack([vector for _ in sentences])


class _EmbeddingProvider:
    embedding_model = "provider-model"

    def __init__(self, vectors: list[list[float]] | Exception) -> None:
        self.vectors = vectors

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        del texts, purpose
        if isinstance(self.vectors, Exception):
            raise self.vectors
        return self.vectors


def _news_items(count: int = 2) -> list[NewsItem]:
    return [
        NewsItem(
            event_id=f"EV-{index}",
            row_number=index + 1,
            published_at=datetime(2030, 1, 10, 8, index, tzinfo=KST),
            title=f"계약 뉴스 {index}",
            body=f"공급 계약 본문 {index}",
            source_id=f"SRC-{index}",
        )
        for index in range(count)
    ]


def _cluster(provider: Any, *, fallback: str = "fail-closed"):
    return asyncio.run(
        cluster_news_events(
            _news_items(),
            window_start_at=datetime(2030, 1, 9, 15, 30, tzinfo=KST),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            embedding_provider=provider,
            embedding_batch_size=8,
            similarity_threshold=0.9,
            fallback_policy=fallback,
        )
    )


def test_local_production_embedding_has_stable_dimension(tmp_path: Path) -> None:
    provider = LocalProductionEmbeddingProvider(
        model=_SentenceModel(),
        model_name="multilingual-model",
        revision="revision",
        artifact_sha256="a" * 64,
        model_path=tmp_path,
        device="cpu",
    )
    vectors = provider.embed_texts(["한국어", "English"])
    assert provider.dimensions == 4
    assert provider.embedding_query_count == 1
    assert provider.embedding_text_count == 2
    assert provider.embedding_input_char_count == sum(len(text) for text in ["한국어", "English"])
    assert {len(vector) for vector in vectors} == {4}
    assert all(math.isclose(sum(value * value for value in vector), 1.0, abs_tol=1e-6) for vector in vectors)


def test_local_production_embedding_is_not_hash_fallback(tmp_path: Path) -> None:
    provider = LocalProductionEmbeddingProvider(
        model=_SentenceModel(),
        model_name="multilingual-model",
        revision="revision",
        artifact_sha256="b" * 64,
        model_path=tmp_path,
        device="cpu",
    )
    assert "deterministic" not in provider.embedding_method
    assert provider.production_capability_attested is True


def test_production_embedding_exception_fails_closed() -> None:
    with pytest.raises(ProductionEmbeddingUnavailableError):
        _cluster(_EmbeddingProvider(RuntimeError("offline")))


def test_production_invalid_vector_count_fails_closed() -> None:
    with pytest.raises(ProductionEmbeddingUnavailableError):
        _cluster(_EmbeddingProvider([[1.0, 0.0]]))


def test_production_nonfinite_vector_fails_closed() -> None:
    with pytest.raises(ProductionEmbeddingUnavailableError):
        _cluster(_EmbeddingProvider([[float("nan")], [1.0]]))


def test_production_dimension_mismatch_fails_closed() -> None:
    with pytest.raises(ProductionEmbeddingUnavailableError):
        _cluster(_EmbeddingProvider([[1.0, 0.0], [1.0]]))


def test_production_failure_emits_no_normal_prediction(tmp_path: Path) -> None:
    csv_path = tmp_path / "news.csv"
    csv_path.write_text(
        'page,row,date,time,title,body\n1,1,"2030-01-10","08:00:00","계약","공급 계약"\n',
        encoding="utf-8",
    )
    settings = Settings(
        project_root=tmp_path,
        embedding_provider="local-production",
        event_cluster_fallback_policy=EmbeddingFallbackPolicy.FAIL_CLOSED,
        web_provider="disabled",
    )
    analyzer = DailyAnalyzer(
        settings,
        embedding_provider=_EmbeddingProvider(RuntimeError("offline")),
    )
    with pytest.raises(ProductionEmbeddingUnavailableError):
        asyncio.run(
            analyzer.analyze(
                news_csv=csv_path,
                trade_date=date(2030, 1, 10),
                cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            )
        )
    assert not (tmp_path / "predictions" / "2030-01-10.json").exists()
    receipts = list((tmp_path / "runs" / "checkpoints" / "failures").glob("*/embedding_failure.json"))
    assert len(receipts) == 1


def test_production_failure_skips_daily_memory_and_final_synthesis(
    tmp_path: Path,
) -> None:
    test_production_failure_emits_no_normal_prediction(tmp_path)
    assert not (tmp_path / "runs" / "daily_memory").exists()
    assert not (tmp_path / "runs" / "checkpoints" / "final_synthesis_context").exists()


def test_mock_mode_can_use_deterministic_fallback() -> None:
    result = _cluster(
        _EmbeddingProvider(RuntimeError("offline")),
        fallback="allow-deterministic-fallback",
    )
    assert result.embedding_status == "DETERMINISTIC_FALLBACK"
    assert result.deterministic_fallback_used is True


def test_doctor_rejects_production_deterministic_fallback(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        embedding_provider="deterministic",
        event_cluster_fallback_policy=(EmbeddingFallbackPolicy.ALLOW_DETERMINISTIC_FALLBACK),
    )
    report = production_readiness_report(
        build_doctor_report(settings, production=True),
        settings,
    )
    assert report["passed"] is False
    assert "embedding: production event clustering must fail closed" in report["findings"]
    assert "embedding: production semantic provider is not configured" in report["findings"]


def test_release_finalize_rejects_embedding_identity_drift(tmp_path: Path) -> None:
    model_dir = tmp_path / "model"
    for relative_path in LOCAL_EMBEDDING_ALLOW_PATTERNS:
        path = model_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            b'{"hidden_size":4}' if relative_path == "config.json" else f"payload:{relative_path}".encode()
        )
    selected_files = selected_model_files(model_dir)
    artifact_hash = selected_artifact_root_sha256(selected_files)
    settings = Settings(
        project_root=tmp_path,
        local_embedding_model="multilingual-model",
        local_embedding_revision="revision",
    )
    manifest = LocalEmbeddingModelManifest(
        model="multilingual-model",
        revision="revision",
        dimension=4,
        model_path=model_dir.as_posix(),
        allow_patterns=list(LOCAL_EMBEDDING_ALLOW_PATTERNS),
        ignore_patterns=[],
        selected_files=selected_files,
        selected_file_count=len(selected_files),
        selected_total_bytes=sum(item.size_bytes for item in selected_files),
        artifact_root_sha256=artifact_hash,
        created_at=now_kst(),
    )
    manifest_path = tmp_path / LOCAL_EMBEDDING_MODEL_MANIFEST_FILE
    write_json(manifest_path, manifest.model_dump(mode="json"))
    write_json(
        tmp_path / LOCAL_EMBEDDING_IDENTITY_FILE,
        {
            "schema_version": "nslab.local_embedding_identity.v2",
            "embedding_provider": "local-production",
            "embedding_model": "multilingual-model",
            "embedding_revision": "revision",
            "embedding_artifact_sha256": artifact_hash,
            "embedding_dimensions": 4,
            "normalization": "l2",
            "model_path": model_dir.as_posix(),
            "embedding_model_manifest_path": (LOCAL_EMBEDDING_MODEL_MANIFEST_FILE.as_posix()),
            "embedding_model_manifest_sha256": file_sha256(manifest_path),
        },
    )
    weight_path = model_dir / "model.safetensors"
    weight_path.write_bytes(b"x" * weight_path.stat().st_size)
    with pytest.raises(
        ProductionEmbeddingUnavailableError,
        match="artifact hash drift",
    ):
        load_local_production_embedding(
            settings,
            model_loader=lambda *args, **kwargs: _SentenceModel(),
        )
