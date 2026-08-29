from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.main import get_command

from news_scalping_lab.cli import app
from news_scalping_lab.contracts.memory_context import PopulationManifest
from news_scalping_lab.memory.index import ProductionMemoryIndex
from news_scalping_lab.memory.population import (
    PopulationRetriever,
    _aggregate_unit,
    _build_cube,
    _outcome_summary,
)
from news_scalping_lab.memory.statistics import (
    UnitObservation,
    observed_rate,
    project_population_record,
    time_slices,
)
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import (
    AsyncEmbeddingProviderAdapter,
    DeterministicHashEmbeddingProvider,
)
from news_scalping_lab.utils import KST, canonical_json, sha256_text


class _EmbeddingBackend:
    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return DeterministicHashEmbeddingProvider().embed_texts(texts)


def _provider() -> AsyncEmbeddingProviderAdapter:
    return AsyncEmbeddingProviderAdapter(
        _EmbeddingBackend(),
        embedding_method="llm_embedding:test:population-v1",
        production_capability_attested=True,
    )


def _record(
    record_id: str,
    *,
    ticker: str,
    record_type: str = "supervised_issuer_day_case",
    response_class: str | None = "POSITIVE",
    high_return_pct: float | None = 12.0,
    training_eligible: bool = True,
    no_catalyst_asserted: bool | None = None,
) -> BrainRecordEnvelope:
    payload: dict[str, object] = {
        "record_type": record_type,
        "training_eligible": training_eligible,
        "ticker": ticker,
        "company_name": "Shared structural issuer",
        "title": "supply agreement confirmed",
        "label_quality": "verified" if high_return_pct is not None else "missing",
    }
    if response_class is not None:
        payload["response_class"] = response_class
    if high_return_pct is not None:
        payload["high_return_pct"] = high_return_pct
    if no_catalyst_asserted is not None:
        payload["no_catalyst_asserted"] = no_catalyst_asserted
    digest = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="NSLAB-20300110-POPULATION",
        trade_date=date(2030, 1, 10),
        available_from=datetime(2030, 1, 10, 20, 0, tzinfo=KST),
        training_target="issuer_day_response",
        evidence_phase="POSTMORTEM",
        training_eligible=training_eligible,
        status="supported" if training_eligible else "tentative",
        confidence_label="high" if training_eligible else "low",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=digest,
        normalized_payload_sha256=digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )


def _build_index(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: list[BrainRecordEnvelope],
) -> tuple[ProductionMemoryIndex, list[BrainRecordEnvelope]]:
    current = list(records)
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(current))
    BrainRecordStore(root).rebuild_indexes()
    index = ProductionMemoryIndex(
        root,
        embedding_provider=_provider(),
        production=True,
    )
    index.build(as_of=datetime(2030, 1, 11, tzinfo=KST))
    return index, current


def _cell_ids(index: ProductionMemoryIndex) -> list[str]:
    manifest = index.resolve_snapshot(
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST)
    )
    path = index.root / manifest.cell_entries.artifact_path
    return [
        str(read_json_line(line)["cell_id"])
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_json_line(line: str) -> dict[str, object]:
    import json

    value = json.loads(line)
    assert isinstance(value, dict)
    return value


def test_population_uses_all_selected_cell_members_and_deduplicates_units(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _record("REC-A1", ticker="000001", high_return_pct=20.0),
        _record("REC-A2", ticker="000001", high_return_pct=20.0),
        _record(
            "REC-B",
            ticker="000002",
            response_class="NEGATIVE",
            high_return_pct=-2.0,
        ),
        _record(
            "REC-MISSING",
            ticker="000003",
            response_class=None,
            high_return_pct=None,
            training_eligible=False,
        ),
        _record(
            "REC-NEWSLESS",
            ticker="000004",
            record_type="newsless_or_unexplained_case",
            response_class=None,
            high_return_pct=18.0,
            no_catalyst_asserted=True,
        ),
    ]
    index, _current = _build_index(tmp_path, monkeypatch, records)
    retriever = PopulationRetriever(tmp_path, memory_index=index)
    result = retriever.build(
        run_id="RUN-POP",
        cluster_id="EVT-POP",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            selected_cell_ids=_cell_ids(index),
            independent_unit_type="issuer-day",
        )

    manifest = result.manifest
    assert manifest.raw_record_count == 3
    assert manifest.independent_unit_count == 2
    assert manifest.outcome_summary.observed_unit_count == 2
    assert manifest.outcome_summary.missing_outcome_unit_count == 0
    assert manifest.polarity_counts["NEGATIVE"] == 1
    assert "UNEXPLAINED" not in manifest.polarity_counts
    assert manifest.observed_rates[1].metric == "high_return_5"
    assert manifest.observed_rates[1].denominator == 2
    assert manifest.observed_rates[1].numerator == 1
    assert retriever.inspect(result.manifest_path)["passed"] is True


def test_population_artifact_tamper_fails_independent_inspection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-A", ticker="000001")],
    )
    retriever = PopulationRetriever(tmp_path, memory_index=index)
    result = retriever.build(
        run_id="RUN-TAMPER",
        cluster_id="EVT-TAMPER",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        selected_cell_ids=_cell_ids(index),
        independent_unit_type="issuer-day",
    )
    cube_path = tmp_path / result.manifest.cube_rows.artifact_path
    cube_path.write_text("{}\n", encoding="utf-8")

    inspection = retriever.inspect(result.manifest_path)

    assert inspection["passed"] is False
    assert "cube_rows_hash_mismatch" in inspection["errors"]
    assert "cube_rows_recompute_mismatch" in inspection["errors"]

    with pytest.raises(ValueError, match="cached population closure is invalid"):
        retriever.build(
            run_id="RUN-TAMPER",
            cluster_id="EVT-TAMPER",
            cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            selected_cell_ids=_cell_ids(index),
            independent_unit_type="issuer-day",
        )


def test_population_build_reuses_verified_content_addressed_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-CACHED", ticker="000001")],
    )
    retriever = PopulationRetriever(tmp_path, memory_index=index)
    kwargs = {
        "run_id": "RUN-CACHED",
        "cluster_id": "EVT-CACHED",
        "cutoff_at": datetime(2030, 1, 11, tzinfo=KST),
        "selected_cell_ids": _cell_ids(index),
        "independent_unit_type": "issuer-day",
    }
    first = retriever.build(**kwargs)  # type: ignore[arg-type]

    def fail_source_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verified population resume must not repeat the source query")

    monkeypatch.setattr(index, "population_members_for_cells", fail_source_query)
    resumed = retriever.build(**kwargs)  # type: ignore[arg-type]

    assert resumed == first


def test_population_rejects_cell_not_present_in_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-A", ticker="000001")],
    )

    with pytest.raises(ValueError, match="absent from the snapshot"):
        PopulationRetriever(tmp_path, memory_index=index).build(
            run_id="RUN-MISSING-CELL",
            cluster_id="EVT-MISSING-CELL",
            cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            selected_cell_ids=[*_cell_ids(index), "CELL-NOT-THERE"],
            independent_unit_type="issuer-day",
        )


def test_population_rejects_path_traversal_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-A", ticker="000001")],
    )

    with pytest.raises(ValueError, match="unsafe path"):
        PopulationRetriever(tmp_path, memory_index=index).build(
            run_id="..",
            cluster_id="EVT-TRAVERSAL",
            cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            selected_cell_ids=_cell_ids(index),
            independent_unit_type="issuer-day",
        )

    assert not (tmp_path / "runs" / "member_records.jsonl").exists()


def test_population_rejects_noncanonical_path_segment_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-A", ticker="000001")],
    )

    with pytest.raises(ValueError, match="unsafe path"):
        PopulationRetriever(tmp_path, memory_index=index).build(
            run_id=" RUN-WHITESPACE ",
            cluster_id="EVT-WHITESPACE",
            cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            selected_cell_ids=_cell_ids(index),
            independent_unit_type="issuer-day",
        )

    assert not (tmp_path / "runs" / "populations" / "RUN-WHITESPACE").exists()


def test_population_manifest_rejects_noncanonical_regime_label(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-A", ticker="000001")],
    )
    result = PopulationRetriever(tmp_path, memory_index=index).build(
        run_id="RUN-REGIME",
        cluster_id="EVT-REGIME",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        selected_cell_ids=_cell_ids(index),
        independent_unit_type="issuer-day",
        query_regime_cluster="RISK_ON",
    )
    payload = result.manifest.model_dump(mode="json")
    payload["query_regime_cluster"] = " risk_on "

    with pytest.raises(ValidationError, match="canonical uppercase"):
        PopulationManifest.model_validate(payload)


def test_population_cli_exposes_purpose_contract() -> None:
    root_command = get_command(app)
    memory_command = root_command.commands["memory"]
    population_command = memory_command.commands["build-population"]
    population_purpose = next(
        parameter
        for parameter in population_command.params
        if parameter.name == "population_purpose"
    )

    assert "--population-purpose" in population_purpose.opts


def test_population_rejects_incompatible_purpose_and_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [_record("REC-A", ticker="000001")],
    )

    with pytest.raises(ValueError, match="incompatible"):
        PopulationRetriever(tmp_path, memory_index=index).build(
            run_id="RUN-BAD-PURPOSE",
            cluster_id="EVT-BAD-PURPOSE",
            cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            selected_cell_ids=_cell_ids(index),
            independent_unit_type="ticker-day",
            population_purpose="catalyst_response",
        )


def test_population_budget_fails_before_member_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [
            _record("REC-A", ticker="000001"),
            _record("REC-B", ticker="000002"),
        ],
    )

    with pytest.raises(ValueError, match="operational record budget"):
        index.population_members_for_cells(
            _cell_ids(index),
            cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
            independent_unit_type="issuer-day",
            max_records=1,
        )


def test_population_member_query_reuses_bounded_runtime_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _current = _build_index(
        tmp_path,
        monkeypatch,
        [
            _record("REC-A", ticker="000001"),
            _record("REC-B", ticker="000002"),
        ],
    )
    kwargs = {
        "cutoff_at": datetime(2030, 1, 11, tzinfo=KST),
        "independent_unit_type": "issuer-day",
    }
    first_snapshot, first_members = index.population_members_for_cells(
        _cell_ids(index),
        **kwargs,  # type: ignore[arg-type]
    )

    def fail_database_connection(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("identical population member query must use its runtime cache")

    monkeypatch.setattr(index, "_runtime_connection", fail_database_connection)
    second_snapshot, second_members = index.population_members_for_cells(
        _cell_ids(index),
        **kwargs,  # type: ignore[arg-type]
    )

    assert second_snapshot == first_snapshot
    assert second_members == first_members


def test_cube_preserves_actual_record_dimension_combinations() -> None:
    unit = _unit("ISSUER_DAY:2030-01-10:000001", high_return_pct=10.0)
    members = [
        _population_member(
            "REC-A",
            unit.independent_unit_id,
            cell_id="CELL-A",
            record_type="supervised_issuer_day_case",
            lane="positive_analogs",
            path_type="DIRECT",
        ),
        _population_member(
            "REC-B",
            unit.independent_unit_id,
            cell_id="CELL-B",
            record_type="candidate_generation_error_case",
            lane="candidate_generation_errors",
            path_type="INFERRED_NEW",
        ),
    ]

    rows = _build_cube(
        members,
        [unit],
        cutoff_date=date(2030, 1, 11),
        query_regime_cluster=None,
    )
    combinations = {
        (row.cell_id, row.record_type, row.memory_lane, row.path_type)
        for row in rows
    }

    assert len(rows) == 6
    assert combinations == {
        ("CELL-A", "supervised_issuer_day_case", "positive_analogs", "DIRECT"),
        ("CELL-B", "candidate_generation_error_case", "candidate_generation_errors", "INFERRED_NEW"),
    }
    assert all(row.raw_record_count == 1 for row in rows)


def test_catalyst_population_excludes_candidate_error_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _record(
            "REC-ISSUER",
            ticker="000001",
            response_class="NEGATIVE",
            high_return_pct=-1.0,
        ),
        _record(
            "REC-CANDIDATE-ERROR",
            ticker="000002",
            record_type="candidate_generation_error_case",
            response_class="NEAR_MISS",
            high_return_pct=4.0,
        ),
    ]
    index, _current = _build_index(tmp_path, monkeypatch, records)
    manifest = PopulationRetriever(tmp_path, memory_index=index).build(
        run_id="RUN-PURPOSE",
        cluster_id="EVT-PURPOSE",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        selected_cell_ids=_cell_ids(index),
        independent_unit_type="issuer-day",
        population_purpose="catalyst_response",
    ).manifest

    assert manifest.raw_record_count == 1
    assert manifest.polarity_counts == {"NEGATIVE": 1}
    assert all(rate.numerator == 0 for rate in manifest.observed_rates)
    assert "candidate_generation_errors" not in manifest.included_memory_lanes


def test_candidate_error_population_is_reachable_without_catalyst_records(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _record("REC-ISSUER", ticker="000001", high_return_pct=12.0),
        _record(
            "REC-CANDIDATE-ERROR",
            ticker="000002",
            record_type="candidate_generation_error_case",
            response_class="NEAR_MISS",
            high_return_pct=4.0,
        ),
    ]
    index, _current = _build_index(tmp_path, monkeypatch, records)
    manifest = PopulationRetriever(tmp_path, memory_index=index).build(
        run_id="RUN-CANDIDATE-PURPOSE",
        cluster_id="EVT-CANDIDATE-PURPOSE",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        selected_cell_ids=_cell_ids(index),
        independent_unit_type="issuer-day",
        population_purpose="candidate_error",
    ).manifest

    assert manifest.raw_record_count == 1
    assert manifest.included_memory_lanes == ["candidate_generation_errors"]
    assert manifest.population_purpose == "candidate_error"

    tampered = manifest.model_dump(mode="json")
    tampered["population_purpose"] = "catalyst_response"
    with pytest.raises(ValidationError, match="conflict with population purpose"):
        PopulationManifest.model_validate(tampered)


def test_nested_outcome_conflict_and_invalid_weight_fail_closed() -> None:
    record = _record("REC-CONFLICT", ticker="000001")
    payload = {
        **record.payload,
        "high_return_pct": 10.0,
        "sample_weight": "NaN",
        "metadata": {"outcome": {"high_return_pct": -10.0}},
    }
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
        }
    )

    projection = project_population_record(record)

    assert projection.high_return_pct is None
    assert projection.outcome_observed is False
    assert projection.sample_weight == 0.0


def test_invalid_record_metric_poisons_same_unit_metric() -> None:
    unit_id = "ISSUER_DAY:2030-01-10:000001"
    valid = _population_member(
        "REC-VALID",
        unit_id,
        cell_id="CELL-A",
        record_type="supervised_issuer_day_case",
        lane="positive_analogs",
        path_type="DIRECT",
    )
    invalid = replace(
        valid,
        record_id="REC-INVALID",
        high_return_pct=None,
        outcome_observed=False,
        high_return_status="INVALID_CONFLICT",
    )

    unit = _aggregate_unit(unit_id, [valid, invalid])

    assert unit.high_return_pct is None
    assert unit.high_return_status == "INVALID_CONFLICT"
    assert unit.outcome_observed is False


def test_conflicting_valid_values_poison_unit_metric_and_weight() -> None:
    unit_id = "ISSUER_DAY:2030-01-10:000001"
    first = _population_member(
        "REC-POSITIVE",
        unit_id,
        cell_id="CELL-A",
        record_type="supervised_issuer_day_case",
        lane="positive_analogs",
        path_type="DIRECT",
    )
    second = replace(
        first,
        record_id="REC-NEGATIVE",
        high_return_pct=-10.0,
        sample_weight=0.25,
    )

    unit = _aggregate_unit(unit_id, [first, second])

    assert unit.high_return_pct is None
    assert unit.high_return_status == "INVALID_CONFLICT"
    assert unit.sample_weight == 0.0
    assert unit.sample_weight_status == "INVALID_CONFLICT"


def test_recursive_conflict_status_survives_database_and_poisons_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = _record("REC-VALID", ticker="000001", high_return_pct=10.0)
    invalid = _record("REC-INVALID", ticker="000001", high_return_pct=10.0)
    invalid_payload = {
        **invalid.payload,
        "metadata": {"outcome": {"high_return_pct": -10.0}},
    }
    digest = sha256_text(canonical_json(invalid_payload))
    invalid = invalid.model_copy(
        update={
            "payload": invalid_payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
        }
    )
    index, _current = _build_index(tmp_path, monkeypatch, [valid, invalid])
    snapshot, members = index.population_members_for_cells(
        _cell_ids(index),
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        independent_unit_type="issuer-day",
    )

    assert snapshot.population_projection_version == "population_statistics.v1"
    assert {member.high_return_status for member in members} == {
        "VALID",
        "INVALID_CONFLICT",
    }
    unit = _aggregate_unit(members[0].independent_unit_id, members)
    assert unit.high_return_pct is None
    assert unit.high_return_status == "INVALID_CONFLICT"


def test_conflicting_unit_regime_is_one_label_not_two_votes() -> None:
    unit_id = "ISSUER_DAY:2030-01-10:000001"
    first = replace(
        _population_member(
            "REC-RISK-ON",
            unit_id,
            cell_id="CELL-A",
            record_type="supervised_issuer_day_case",
            lane="positive_analogs",
            path_type="DIRECT",
        ),
        regime_cluster="RISK_ON",
    )
    second = replace(first, record_id="REC-RISK-OFF", regime_cluster="RISK_OFF")

    unit = _aggregate_unit(unit_id, [first, second])

    assert unit.regime_clusters == ("CONFLICTING",)
    cube = _build_cube(
        [first, second],
        [unit],
        cutoff_date=date(2030, 1, 11),
        query_regime_cluster="RISK_ON",
    )
    assert {row.regime_cluster for row in cube} == {"CONFLICTING"}
    assert all(row.time_slice != "SIMILAR_REGIME" for row in cube)


def test_rounded_weight_aliases_remain_usable() -> None:
    record = _record("REC-ROUNDED-WEIGHT", ticker="000001")
    payload = {
        **record.payload,
        "sample_weight": 0.333334,
        "fields": {"sample_weight": 0.333333},
    }
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
        }
    )

    assert project_population_record(record).sample_weight == pytest.approx(0.333333)


def test_explicit_outcome_observed_conflict_fails_closed() -> None:
    record = _record("REC-OBSERVED-CONFLICT", ticker="000001")
    payload = {**record.payload, "outcome_observed": False}
    digest = sha256_text(canonical_json(payload))
    record = record.model_copy(
        update={
            "payload": payload,
            "raw_payload_sha256": digest,
            "normalized_payload_sha256": digest,
        }
    )

    projection = project_population_record(record)

    assert projection.high_return_pct is None
    assert projection.outcome_observed is False


def test_upper_limit_flag_does_not_invent_numeric_high_return() -> None:
    unit = _unit("UNIT-UPPER", high_return_pct=None)
    unit = replace(unit, upper_limit_touched=True, upper_limit_status="VALID")

    summary = _outcome_summary([unit])

    assert summary.upper_limit_touched_count == 1
    assert summary.high_return_5_count == 0
    assert summary.high_return_10_count == 0
    assert summary.high_return_20_count == 0


def test_outcome_distribution_uses_independent_unit_weights() -> None:
    units = [
        _unit(
            f"EVENT-{index}",
            high_return_pct=20.0,
            sample_weight=0.1,
        )
        for index in range(10)
    ] + [_unit("EVENT-NEGATIVE", high_return_pct=-10.0, sample_weight=1.0)]

    summary = _outcome_summary(units)

    assert summary.mean_high_return_pct == pytest.approx(5.0)
    assert summary.distribution_weighting == "independent_unit_sample_weight.v1"


def test_invalid_weight_outcome_is_missing_everywhere() -> None:
    unit = replace(
        _unit("UNIT-INVALID-WEIGHT", high_return_pct=20.0),
        sample_weight=0.0,
        sample_weight_status="INVALID_CONFLICT",
        upper_limit_touched=True,
        upper_limit_status="VALID",
    )

    summary = _outcome_summary([unit])
    rate = observed_rate(
        [unit],
        metric="high_return_5",
        seed=3,
        bootstrap_iterations=20,
    )

    assert summary.observed_unit_count == 0
    assert summary.missing_outcome_unit_count == 1
    assert summary.upper_limit_touched_count == 0
    assert summary.mean_high_return_pct is None
    assert rate.denominator == 0


def test_observed_rate_does_not_treat_missing_outcome_as_negative() -> None:
    units = [
        _unit("UNIT-OBSERVED", high_return_pct=10.0),
        _unit("UNIT-MISSING", high_return_pct=None),
    ]

    rate = observed_rate(units, metric="high_return_5", seed=1, bootstrap_iterations=20)

    assert rate.numerator == 1
    assert rate.denominator == 1
    assert rate.observed_rate == 1.0


def test_future_trade_date_cannot_be_labeled_all_history() -> None:
    with pytest.raises(ValueError, match="after the analysis cutoff"):
        time_slices(date(2030, 1, 12), cutoff_date=date(2030, 1, 11))


def test_observed_rate_respects_independent_unit_weights() -> None:
    units = [
        _unit("UNIT-POSITIVE", high_return_pct=10.0, sample_weight=0.25),
        _unit("UNIT-NEGATIVE", high_return_pct=-1.0, sample_weight=1.0),
    ]

    rate = observed_rate(units, metric="high_return_5", seed=2, bootstrap_iterations=20)

    assert rate.numerator == 1
    assert rate.denominator == 2
    assert rate.weighted_numerator == 0.25
    assert rate.weighted_denominator == 1.25
    assert rate.observed_rate == pytest.approx(0.2)


def test_incremental_and_full_snapshot_population_are_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _record("REC-ONE", ticker="000001", high_return_pct=12.0)
    second = _record(
        "REC-TWO",
        ticker="000002",
        response_class="NEGATIVE",
        high_return_pct=-1.0,
    )
    incremental_root = tmp_path / "incremental"
    full_root = tmp_path / "full"
    incremental_index, incremental_records = _build_index(
        incremental_root,
        monkeypatch,
        [first],
    )
    incremental_records.append(second)
    BrainRecordStore(incremental_root).rebuild_indexes()
    incremental_index.build(as_of=datetime(2030, 1, 11, tzinfo=KST))
    incremental_result = PopulationRetriever(
        incremental_root,
        memory_index=incremental_index,
    ).build(
        run_id="RUN-PARITY",
        cluster_id="EVT-PARITY",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        selected_cell_ids=_cell_ids(incremental_index),
        independent_unit_type="issuer-day",
    )

    full_index, _full_records = _build_index(
        full_root,
        monkeypatch,
        [first, second],
    )
    full_result = PopulationRetriever(
        full_root,
        memory_index=full_index,
    ).build(
        run_id="RUN-PARITY",
        cluster_id="EVT-PARITY",
        cutoff_at=datetime(2030, 1, 11, tzinfo=KST),
        selected_cell_ids=_cell_ids(full_index),
        independent_unit_type="issuer-day",
    )

    incremental = incremental_result.manifest
    full = full_result.manifest
    assert incremental.population_id == full.population_id
    assert incremental.memory_snapshot_id == full.memory_snapshot_id
    assert incremental.member_records.sha256 == full.member_records.sha256
    assert incremental.independent_units.sha256 == full.independent_units.sha256
    assert incremental.cube_rows.sha256 == full.cube_rows.sha256
    assert incremental.outcome_summary == full.outcome_summary
    assert incremental.observed_rates == full.observed_rates


def test_bootstrap_statistics_do_not_depend_on_run_or_cluster_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    records = [
        _record(f"REC-{index}", ticker=f"{index:06d}", high_return_pct=float(index))
        for index in range(1, 10)
    ]
    index, _current = _build_index(tmp_path, monkeypatch, records)
    retriever = PopulationRetriever(tmp_path, memory_index=index)
    common = {
        "cutoff_at": datetime(2030, 1, 11, tzinfo=KST),
        "selected_cell_ids": _cell_ids(index),
        "independent_unit_type": "issuer-day",
    }

    first = retriever.build(
        run_id="RUN-SEED-A",
        cluster_id="EVT-SEED-A",
        **common,
    ).manifest
    second = retriever.build(
        run_id="RUN-SEED-B",
        cluster_id="EVT-SEED-B",
        **common,
    ).manifest

    assert first.population_id != second.population_id
    assert first.observed_rates == second.observed_rates
    assert first.cube_rows.sha256 == second.cube_rows.sha256


def _unit(
    unit_id: str,
    *,
    high_return_pct: float | None,
    sample_weight: float = 1.0,
) -> UnitObservation:
    return UnitObservation(
        independent_unit_id=unit_id,
        trade_date=date(2030, 1, 10),
        record_ids=(unit_id,),
        cell_ids=("CELL-1",),
        memory_lanes=("positive_analogs",),
        record_types=("supervised_issuer_day_case",),
        path_types=("UNKNOWN",),
        regime_clusters=("UNKNOWN",),
        polarity="POSITIVE" if high_return_pct is not None else "UNKNOWN",
        eligibility="ELIGIBLE",
        label_quality="verified" if high_return_pct is not None else "missing",
        sample_weight=sample_weight,
        high_return_pct=high_return_pct,
        close_return_pct=None,
        upper_limit_touched=None,
        high_return_status="VALID" if high_return_pct is not None else "MISSING",
        close_return_status="MISSING",
        upper_limit_status="MISSING",
        sample_weight_status="VALID",
    )


def _population_member(
    record_id: str,
    unit_id: str,
    *,
    cell_id: str,
    record_type: str,
    lane: str,
    path_type: str,
):
    from news_scalping_lab.memory.index import PopulationCellMember

    return PopulationCellMember(
        record_id=record_id,
        independent_unit_id=unit_id,
        independent_unit_type="issuer-day",
        primary_cell_id=cell_id,
        matched_cell_ids=(cell_id,),
        trade_date=date(2030, 1, 10),
        record_type=record_type,
        training_eligible=True,
        routing_disposition="REASONING",
        evidence_polarity="POSITIVE",
        label_quality="verified",
        memory_lanes=(lane,),
        path_type=path_type,
        regime_cluster="UNKNOWN",
        high_return_pct=10.0,
        close_return_pct=None,
        upper_limit_touched=None,
        outcome_observed=True,
        sample_weight=1.0,
        high_return_status="VALID",
        close_return_status="MISSING",
        upper_limit_status="MISSING",
        sample_weight_status="DEFAULT",
    )
