from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path
from time import perf_counter

from news_scalping_lab.config import Settings
from news_scalping_lab.policies import EmbeddingFallbackPolicy
from news_scalping_lab.retrieval.production_embedding import (
    LOCAL_EMBEDDING_MODEL_MANIFEST_FILE,
    load_local_production_embedding,
    prepare_local_production_embedding,
    verify_local_production_embedding,
)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="nslab-embedding-clean-smoke-") as root:
        settings = Settings(
            project_root=Path(root),
            embedding_provider="local-production",
            event_cluster_fallback_policy=EmbeddingFallbackPolicy.FAIL_CLOSED,
            local_embedding_cache_path=Path("clean-cache"),
        )
        started = perf_counter()
        identity = prepare_local_production_embedding(settings)
        prepare_seconds = perf_counter() - started
        provider = load_local_production_embedding(settings)
        vectors = provider.embed_texts(
            ["한국 상장사 공급계약", "English listed-company supply contract"]
        )
        deep = verify_local_production_embedding(settings, deep=True)
        fast = verify_local_production_embedding(settings, deep=False)
        manifest = deep["manifest"]
        result = {
            "schema_version": "nslab.local_embedding_clean_cache_smoke.v1",
            "passed": True,
            "model": manifest.model,
            "revision": manifest.revision,
            "dimension": provider.dimensions,
            "vector_count": len(vectors),
            "finite": all(
                math.isfinite(value) for vector in vectors for value in vector
            ),
            "selected_file_count": manifest.selected_file_count,
            "selected_total_bytes": manifest.selected_total_bytes,
            "full_repository_size_if_known": (
                manifest.full_repository_size_if_known
            ),
            "excluded_file_count": manifest.excluded_file_count,
            "artifact_root_sha256": manifest.artifact_root_sha256,
            "manifest_sha256": deep["manifest_sha256"],
            "manifest_relative_path": (
                LOCAL_EMBEDDING_MODEL_MANIFEST_FILE.as_posix()
            ),
            "prepare_download_and_load_seconds": prepare_seconds,
            "deep_verification_seconds": deep["verification_seconds"],
            "fast_verification_seconds": fast["verification_seconds"],
            "first_fast_verification_seconds": identity[
                "fast_verification_seconds"
            ],
            "model_load_seconds": identity["model_load_seconds"],
            "deep_hashed_file_count": deep["hashed_file_count"],
            "fast_hashed_file_count_after_process_cache": fast[
                "hashed_file_count"
            ],
            "peak_memory_if_measured": None,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
