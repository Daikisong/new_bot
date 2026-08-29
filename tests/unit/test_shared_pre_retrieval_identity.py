from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

import news_scalping_lab.evaluation.shared_pre_retrieval as shared_module
import news_scalping_lab.inference.event_clustering as event_clustering_module
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import (
    NewsNoveltyFinding,
    NewsNoveltyLabel,
    NewsNoveltyReview,
    OpenWorldClusterFinding,
    OpenWorldFirstAnalysis,
)
from news_scalping_lab.contracts.quality_evaluation import (
    QualityArtifactReference,
    SharedMapReduceNode,
    SharedPreRetrievalContext,
)
from news_scalping_lab.inference.event_clustering import OpenWorldClusterInput
from news_scalping_lab.utils import KST, sha256_bytes


def test_shared_map_batches_honor_configured_cluster_limit() -> None:
    settings = Settings()
    settings.limits.open_world_cluster_batch_size = 3
    analyzer = SimpleNamespace(settings=settings)
    clusters = [
        OpenWorldClusterInput(
            cluster_id=f"CLUSTER-{index:02d}",
            representative_text=f"event {index}",
            member_news=(f"event {index}",),
            event_ids=(f"EVT-{index:02d}",),
            row_numbers=(index + 1,),
        )
        for index in range(8)
    ]

    batches = shared_module._map_batches(
        analyzer,
        clusters=clusters,
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert [len(batch) for batch in batches] == [3, 3, 2]
    assert [
        cluster.cluster_id for batch in batches for cluster in batch
    ] == [cluster.cluster_id for cluster in clusters]


def test_event_clustering_transitive_helper_changes_semantic_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = shared_module._event_cluster_renderer_sha256()
    original = event_clustering_module._cosine

    def revised_cosine(left: list[float], right: list[float]) -> float:
        return original(left, right)

    monkeypatch.setattr(event_clustering_module, "_cosine", revised_cosine)

    assert shared_module._event_cluster_renderer_sha256() != before


def test_component_output_sha_changes_final_content_identity() -> None:
    reference = QualityArtifactReference(
        artifact_path="runs/shared/component.json",
        sha256="a" * 64,
    )
    node = SharedMapReduceNode(
        node_id="NODE-1",
        level=0,
        kind="MAP",
        covered_cluster_ids=["CLUSTER-1"],
        prompt_sha256="b" * 64,
        output=reference,
        prompt_tokens=1,
        completion_tokens=1,
        live_call_count=1,
    )
    first_root = shared_module._component_artifact_root_sha256(
        references={"map_reduce_output:NODE-1": reference},
        map_reduce_nodes=[node],
    )
    first_identity = shared_module._content_identity_sha256(
        lookup_identity_sha256="c" * 64,
        parsed_news_root_sha256="d" * 64,
        input_cluster_root_sha256="e" * 64,
        prompt_sha256_root="f" * 64,
        component_artifact_root_sha256=first_root,
        downstream_digest_payload_sha256="1" * 64,
        context_payload_sha256="2" * 64,
    )
    revised_reference = reference.model_copy(update={"sha256": "3" * 64})
    revised_node = node.model_copy(update={"output": revised_reference})
    revised_root = shared_module._component_artifact_root_sha256(
        references={"map_reduce_output:NODE-1": revised_reference},
        map_reduce_nodes=[revised_node],
    )
    revised_identity = shared_module._content_identity_sha256(
        lookup_identity_sha256="c" * 64,
        parsed_news_root_sha256="d" * 64,
        input_cluster_root_sha256="e" * 64,
        prompt_sha256_root="f" * 64,
        component_artifact_root_sha256=revised_root,
        downstream_digest_payload_sha256="1" * 64,
        context_payload_sha256="2" * 64,
    )

    assert revised_root != first_root
    assert revised_identity != first_identity


def test_shared_artifact_write_is_create_or_verify(tmp_path: Path) -> None:
    path = tmp_path / "component.json"
    shared_module._write_immutable_bytes(path, b'{"value":1}\n')
    shared_module._write_immutable_bytes(path, b'{"value":1}\n')

    with pytest.raises(ValueError, match="immutable shared pre-retrieval"):
        shared_module._write_immutable_bytes(path, b'{"value":2}\n')


def test_reference_hash_and_parse_use_one_read_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "runs" / "shared" / "component.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b'{"safe":{"value":1}}\n')
    target = path.resolve()
    safe_bytes = path.read_bytes()
    malicious_bytes = b'{"safe":{"truth":{"winner":"leak"}}}\n'
    reference = QualityArtifactReference(
        artifact_path=path.relative_to(tmp_path).as_posix(),
        sha256=sha256_bytes(safe_bytes),
    )
    original_read_bytes = Path.read_bytes
    target_read_count = 0

    def swapping_read_bytes(candidate: Path) -> bytes:
        nonlocal target_read_count
        if candidate.resolve() == target:
            target_read_count += 1
            return safe_bytes if target_read_count == 1 else malicious_bytes
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", swapping_read_bytes)
    artifact = shared_module._resolve_shared_reference(
        tmp_path.resolve(),
        reference=reference,
    )

    assert target_read_count == 1
    assert artifact.raw_bytes == safe_bytes
    assert artifact.payload == {"safe": {"value": 1}}


@pytest.mark.parametrize("suffix", [".json", ".jsonl"])
@pytest.mark.parametrize(
    "forbidden_key",
    [
        "next_day_return_pct",
        "realizedReturnPct",
        "winnerFlag",
        "actualOutcome",
        "nextDayReturn",
        "dDayHighReturn",
    ],
)
def test_shared_raw_payload_scan_rejects_nested_result_aliases(
    tmp_path: Path,
    suffix: str,
    forbidden_key: str,
) -> None:
    path = tmp_path / f"component{suffix}"
    path.write_text(
        '{"safe":{"nested":{"' + forbidden_key + '":12.3}}}\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="forbidden outcome fields|result-label aliases"):
        shared_module._scan_blind_json_artifact(path)


def test_downstream_digest_must_rederive_from_root_and_novelty() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    open_world_reference = QualityArtifactReference(
        artifact_path="runs/shared/open_world.json",
        sha256="a" * 64,
    )
    novelty_reference = QualityArtifactReference(
        artifact_path="runs/shared/novelty.json",
        sha256="b" * 64,
    )
    output_reference = QualityArtifactReference(
        artifact_path="runs/shared/root.json",
        sha256="c" * 64,
    )
    root_contract = SharedMapReduceNode(
        node_id="ROOT-1",
        level=0,
        kind="MAP",
        covered_cluster_ids=["CLUSTER-1"],
        prompt_sha256="d" * 64,
        output=output_reference,
        prompt_tokens=1,
        completion_tokens=1,
        live_call_count=1,
    )
    root_output = OpenWorldFirstAnalysis(
        run_id="RUN-1",
        prompt_version="test",
        prompt_sha256="d" * 64,
        created_at=cutoff,
        cutoff_at=cutoff,
        source_cluster_ids=["CLUSTER-1"],
        analyzed_cluster_ids=["CLUSTER-1"],
        analysis_batch_count=1,
        cluster_findings=[
            OpenWorldClusterFinding(
                cluster_id="CLUSTER-1",
                event_summary="event",
                mechanisms=["mechanism"],
            )
        ],
        mechanisms=["mechanism"],
    )
    novelty = NewsNoveltyReview(
        run_id="RUN-1",
        prompt_version="test",
        prompt_sha256="e" * 64,
        created_at=cutoff,
        cutoff_at=cutoff,
        review_mode="CSV_MEMORY_ONLY_STRICT",
        cluster_count=1,
        reviewed_cluster_count=1,
        findings=[
            NewsNoveltyFinding(
                cluster_id="CLUSTER-1",
                cluster_index=1,
                novelty=NewsNoveltyLabel.NEW,
            )
        ],
    )
    root_state = shared_module._NodeState(
        contract=root_contract,
        output=root_output,
    )
    digest = shared_module._shared_downstream_digest(
        context_id="SHAREDCTX-1",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff,
        material_cluster_ids=["CLUSTER-1"],
        root_state=root_state,
        open_world_source=open_world_reference,
        novelty=novelty,
        novelty_source=novelty_reference,
    )
    context = cast(
        SharedPreRetrievalContext,
        SimpleNamespace(
            context_id="SHAREDCTX-1",
            trade_date=date(2030, 1, 10),
            cutoff_at=cutoff,
            material_cluster_ids=["CLUSTER-1"],
            open_world_first_analysis=open_world_reference,
            news_novelty_review=novelty_reference,
        ),
    )
    shared_module._verify_downstream_digest(
        context=context,
        actual=digest,
        root_state=root_state,
        novelty=novelty,
    )
    tampered_root = dict(digest.open_world_root)
    tampered_root["mechanisms"] = ["coordinated tamper"]
    tampered = digest.model_copy(update={"open_world_root": tampered_root})

    with pytest.raises(ValueError, match="root-node and novelty sources"):
        shared_module._verify_downstream_digest(
            context=context,
            actual=tampered,
            root_state=root_state,
            novelty=novelty,
        )
