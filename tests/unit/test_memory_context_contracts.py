from datetime import date, datetime

import pytest
from pydantic import ValidationError

from news_scalping_lab.contracts.memory_context import (
    ArtifactReference,
    DailyMemoryContext,
    EventClusterEntry,
    MemoryCoverageManifest,
    NewsCoverageManifest,
    NewsRowCoverage,
    PopulationManifest,
    PopulationOutcomeSummary,
    RecordRoutingMetadata,
)
from news_scalping_lab.contracts.models import (
    ContextManifest,
    OpenWorldClusterFinding,
    PriceSnapshot,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def test_record_routing_metadata_is_strict_and_versioned() -> None:
    payload = {
        "record_id": "REC-1",
        "record_type": "negative_control_case",
        "available_from": datetime(2030, 1, 11, tzinfo=KST),
        "evidence_polarity": "NEGATIVE",
        "training_eligible": True,
        "label_quality": "verified",
        "routing_disposition": "REASONING",
        "memory_lanes": ["negative_controls"],
        "polarity_classifier_version": "record_polarity.v1",
        "threshold_source": "explicit_label",
        "threshold_role": "explicit_label",
        "provenance_source_ids": ["SRC-1"],
    }

    model = RecordRoutingMetadata.model_validate(payload)

    assert model.schema_version == "nslab.record_routing_metadata.v1"
    with pytest.raises(ValidationError):
        RecordRoutingMetadata.model_validate({**payload, "unexpected": True})


def test_daily_memory_context_contains_hash_references_not_raw_corpus() -> None:
    reference = ArtifactReference(
        artifact_path="runs/context/coverage.json",
        sha256="a" * 64,
        item_count=10,
    )

    context = DailyMemoryContext(
        run_id="RUN-1",
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, tzinfo=KST),
        corpus_manifest_sha256="b" * 64,
        news_coverage_manifest=reference,
        event_cluster_manifest=reference,
        memory_coverage_manifest=reference,
        estimated_token_count=0,
        context_complete=False,
    )

    dumped = context.model_dump(mode="json")
    assert dumped["schema_version"] == "nslab.daily_memory_context.v1"
    assert "records" not in dumped
    assert dumped["memory_coverage_manifest"]["sha256"] == "a" * 64


def test_hashes_and_datetimes_reject_unsafe_values() -> None:
    with pytest.raises(ValidationError):
        ArtifactReference(
            artifact_path="coverage.json",
            sha256="not-a-sha256",
            item_count=1,
        )
    with pytest.raises(ValidationError):
        RecordRoutingMetadata(
            record_id="REC-1",
            record_type="memory_claim",
            available_from=datetime(2030, 1, 11),
            evidence_polarity="CONTEXT",
            training_eligible=True,
            label_quality="verified",
            routing_disposition="CONTEXT",
            polarity_classifier_version="explicit.v1",
            threshold_source="explicit",
            threshold_role="explicit_label",
        )


def test_news_coverage_rejects_count_and_assignment_conflicts() -> None:
    row = NewsRowCoverage(
        row_number=1,
        event_id="EV-1",
        source_id="SRC-1",
        primary_cluster_id="CL-1",
        disposition="MATERIAL_FULL_RETRIEVAL",
    )
    with pytest.raises(ValidationError):
        NewsCoverageManifest(
            run_id="RUN-1",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, tzinfo=KST),
            input_news_sha256="a" * 64,
            input_row_count=2,
            covered_row_count=1,
            missing_row_count=0,
            duplicate_assignment_count=0,
            disposition_counts={"MATERIAL_FULL_RETRIEVAL": 1},
            row_coverage_sha256="b" * 64,
            rows=[row],
        )
    with pytest.raises(ValidationError):
        NewsRowCoverage(
            row_number=1,
            event_id="EV-1",
            source_id="SRC-1",
            primary_cluster_id="CL-1",
            duplicate_parent_cluster_id="CL-0",
            disposition="DUPLICATE",
        )


def test_event_cluster_uses_row_identity_for_exact_duplicate_articles() -> None:
    cluster = EventClusterEntry(
        cluster_id="CL-1",
        representative_event_id="EV-SAME",
        member_event_ids=["EV-SAME", "EV-SAME"],
        member_source_ids=["SRC-1", "SRC-2"],
        member_row_numbers=[1, 2],
        disposition="MATERIAL_FULL_RETRIEVAL",
        exact_duplicate_count=1,
        cluster_signature_sha256="a" * 64,
    )

    assert cluster.member_row_numbers == [1, 2]
    with pytest.raises(ValidationError):
        EventClusterEntry(
            cluster_id="CL-1",
            representative_event_id="EV-SAME",
            member_event_ids=["EV-SAME"],
            member_source_ids=["SRC-1", "SRC-2"],
            member_row_numbers=[1, 2],
            disposition="MATERIAL_FULL_RETRIEVAL",
            cluster_signature_sha256="a" * 64,
        )


def test_open_world_cluster_finding_rejects_blank_semantics() -> None:
    with pytest.raises(ValidationError):
        OpenWorldClusterFinding(
            cluster_id="CL-1",
            event_summary="event",
            mechanisms=["   "],
            uncertainties=[],
        )


def test_memory_coverage_rejects_false_complete_claim() -> None:
    reference = ArtifactReference(
        artifact_path="records.jsonl",
        sha256="a" * 64,
        item_count=1,
    )
    with pytest.raises(ValidationError):
        MemoryCoverageManifest(
            run_id="RUN-1",
            cutoff_at=datetime(2030, 1, 10, 8, 59, tzinfo=KST),
            corpus_manifest_sha256="b" * 64,
            accepted_record_count=1,
            available_record_count=1,
            future_record_count=0,
            missing_record_count=1,
            unexpected_record_count=0,
            duplicate_record_count=0,
            available_record_ids=reference,
            record_hash_manifest=reference,
            coverage_complete=True,
        )


def test_population_rejects_impossible_outcome_counts() -> None:
    with pytest.raises(ValidationError):
        PopulationManifest(
            population_id="POP-1",
            run_id="RUN-1",
            cluster_id="CL-1",
            cutoff_at=datetime(2030, 1, 10, 8, 59, tzinfo=KST),
            corpus_manifest_sha256="a" * 64,
            membership_manifest_sha256="b" * 64,
            independent_unit_type="issuer-day",
            raw_record_count=2,
            independent_unit_count=2,
            effective_sample_size=2.0,
            polarity_counts={"POSITIVE": 2},
            eligibility_counts={"eligible": 2},
            label_quality_counts={"verified": 2},
            outcome_summary=PopulationOutcomeSummary(
                observed_unit_count=1,
                missing_outcome_unit_count=0,
                upper_limit_touched_count=0,
                high_return_5_count=0,
                high_return_10_count=0,
                high_return_20_count=0,
            ),
        )


def test_context_manifest_binds_batch_prompt_hashes_to_aggregate() -> None:
    batches = ["a" * 64, "b" * 64]
    aggregate = sha256_text(canonical_json(batches))
    common = {
        "run_id": "RUN-1",
        "mode": "fast",
        "trade_date": date(2030, 1, 10),
        "cutoff_at": datetime(2030, 1, 10, 8, 59, tzinfo=KST),
        "as_of": datetime(2030, 1, 10, 8, 59, tzinfo=KST),
        "accepted_episode_count": 0,
        "swept_episode_count": 0,
        "price_snapshot": PriceSnapshot(source_name="mock"),
    }

    manifest = ContextManifest(
        **common,
        prompt_hashes={"open_world_first_analysis": aggregate},
        prompt_batch_hashes={"open_world_first_analysis": batches},
    )

    assert manifest.prompt_batch_hashes["open_world_first_analysis"] == batches
    with pytest.raises(ValidationError):
        ContextManifest(
            **common,
            prompt_hashes={"open_world_first_analysis": "c" * 64},
            prompt_batch_hashes={"open_world_first_analysis": batches},
        )
