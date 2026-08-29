from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from typer.main import get_command

from news_scalping_lab.cli import app
from news_scalping_lab.llm.base import conservative_token_upper_bound
from news_scalping_lab.memory import diversity as diversity_module
from news_scalping_lab.memory.adaptive_retrieval import AdaptiveRetriever, _expansion_query
from news_scalping_lab.memory.diversity import (
    REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION,
    RepresentativeSelectionBudgetError,
    RepresentativeSelector,
    _Candidate,
    _mmr_select,
    _normalized_cosine,
    _pairwise_normalized_cosines,
    _representative_row,
    _stratified_candidate_pool,
    _unit_candidates,
)
from news_scalping_lab.memory.index import (
    ProductionMemoryIndex,
    RepresentativeSourceRecord,
)
from news_scalping_lab.memory.population import PopulationRetriever
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.retrieval.embedding import (
    AsyncEmbeddingProviderAdapter,
    DeterministicHashEmbeddingProvider,
)
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    read_json,
    sha256_text,
    write_json,
)


class _EmbeddingBackend:
    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        return DeterministicHashEmbeddingProvider().embed_texts(texts)


def _provider() -> AsyncEmbeddingProviderAdapter:
    return AsyncEmbeddingProviderAdapter(
        _EmbeddingBackend(),
        embedding_method="llm_embedding:test:representative-v1",
        production_capability_attested=True,
    )


def test_pairwise_similarity_matrix_preserves_cosine_and_zero_vector_semantics() -> None:
    vectors = [
        (1.0, 0.0),
        (0.0, 1.0),
        (-1.0, 0.0),
        (0.0, 0.0),
    ]

    observed = _pairwise_normalized_cosines(vectors)

    for row_index, left in enumerate(vectors):
        for column_index, right in enumerate(vectors):
            assert observed[row_index, column_index] == pytest.approx(
                _normalized_cosine(left, right)
            )


def _record(
    index: int,
    *,
    trade_date: date,
    high_return_pct: float,
) -> BrainRecordEnvelope:
    record_id = f"REC-{index:03d}"
    payload = {
        "record_type": "supervised_issuer_day_case",
        "training_eligible": True,
        "ticker": f"{index:06d}",
        "company_name": f"Issuer {index}",
        "title": f"supply agreement mechanism pattern {index}",
        "response_class": "POSITIVE" if high_return_pct >= 5.0 else "NEGATIVE",
        "high_return_pct": high_return_pct,
        "label_quality": "verified",
        "path_type": "DIRECT" if index % 2 else "INFERRED_NEW",
        "regime_cluster": "RISK_ON" if index % 3 else "RISK_OFF",
    }
    digest = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="supervised_issuer_day_case",
        episode_id="NSLAB-REPRESENTATIVE",
        trade_date=trade_date,
        available_from=datetime.combine(
            trade_date,
            datetime.min.time(),
            tzinfo=KST,
        ),
        training_target="issuer_day_response",
        evidence_phase="POSTMORTEM",
        training_eligible=True,
        status="supported",
        confidence_label="high",
        provenance_source_ids=[f"SRC-{record_id}"],
        raw_payload_sha256=digest,
        normalized_payload_sha256=digest,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        payload=payload,
    )


def _build_population(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProductionMemoryIndex, Path]:
    cutoff = datetime(2030, 1, 20, tzinfo=KST)
    records = [
        _record(
            index,
            trade_date=date(2030, 1, 10) - timedelta(days=index % 4),
            high_return_pct=15.0 if index % 3 == 0 else -2.0 if index % 3 == 1 else 7.0,
        )
        for index in range(1, 19)
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    BrainRecordStore(root).rebuild_indexes()
    index = ProductionMemoryIndex(
        root,
        embedding_provider=_provider(),
        production=True,
    )
    snapshot = index.build(as_of=cutoff)
    cell_path = root / snapshot.cell_entries.artifact_path
    import json

    cell_ids = [
        str(json.loads(line)["cell_id"])
        for line in cell_path.read_text(encoding="utf-8").splitlines()
    ]
    population = PopulationRetriever(root, memory_index=index).build(
        run_id="RUN-REPRESENTATIVE",
        cluster_id="EVT-REPRESENTATIVE",
        cutoff_at=cutoff,
        selected_cell_ids=cell_ids,
        independent_unit_type="issuer-day",
    )
    return index, population.manifest_path


def test_representative_selection_preserves_units_strata_and_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, population_path = _build_population(tmp_path, monkeypatch)
    selector = RepresentativeSelector(tmp_path, memory_index=index)

    result = selector.build(
        population_manifest_path=population_path,
        query="supply agreement market response",
    )

    manifest = result.manifest
    assert manifest.selected_record_count == manifest.selected_unit_count
    assert manifest.selected_record_count <= REPRESENTATIVE_MAX_TRADE_DATE_CONCENTRATION * 4
    assert manifest.estimated_token_count <= manifest.max_token_count
    records_path = tmp_path / manifest.representative_records.artifact_path
    assert manifest.estimated_token_count == len(records_path.read_bytes())
    assert manifest.diversity_coverage_ratio > 0.0
    assert {item.stratum for item in manifest.strata} >= {
        "outcome:SUCCESS_HIGH10",
        "outcome:NEGATIVE",
        "outcome:POSITIVE_LOW",
    }
    strata = {item.stratum: item for item in manifest.strata}
    assert strata["outcome:SUCCESS_HIGH10"].selected_unit_count >= 1
    assert strata["outcome:NEGATIVE"].selected_unit_count >= 1
    assert strata["outcome:POSITIVE_LOW"].selected_unit_count >= 1
    assert selector.inspect(result.manifest_path)["passed"] is True


def test_representative_source_query_reuses_bounded_runtime_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, _population_path = _build_population(tmp_path, monkeypatch)
    record_ids = [f"REC-{value:03d}" for value in range(1, 19)]
    first_snapshot, first_rows = index.representative_source_records(
        record_ids,
        cutoff_at=datetime(2030, 1, 20, tzinfo=KST),
    )

    def fail_database_connection(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("identical representative source query must use its cache")

    monkeypatch.setattr(index, "_runtime_connection", fail_database_connection)
    second_snapshot, second_rows = index.representative_source_records(
        record_ids,
        cutoff_at=datetime(2030, 1, 20, tzinfo=KST),
    )

    assert second_snapshot == first_snapshot
    assert second_rows == first_rows


def test_representative_inspection_detects_artifact_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, population_path = _build_population(tmp_path, monkeypatch)
    selector = RepresentativeSelector(tmp_path, memory_index=index)
    result = selector.build(
        population_manifest_path=population_path,
        query="supply agreement market response",
    )
    records_path = tmp_path / result.manifest.representative_records.artifact_path
    records_path.write_text("{}\n", encoding="utf-8")

    inspection = selector.inspect(result.manifest_path)

    assert inspection["passed"] is False
    assert "representative_records_hash_mismatch" in inspection["errors"]
    with pytest.raises(ValueError, match="cached representative closure is invalid"):
        selector.build(
            population_manifest_path=population_path,
            query="supply agreement market response",
        )


def test_representative_selection_is_deterministic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, population_path = _build_population(tmp_path, monkeypatch)
    selector = RepresentativeSelector(tmp_path, memory_index=index)

    first = selector.build(
        population_manifest_path=population_path,
        query="supply agreement market response",
    )

    def fail_source_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verified representative resume must not repeat the source query")

    monkeypatch.setattr(index, "representative_source_records", fail_source_query)
    second = selector.build(
        population_manifest_path=population_path,
        query="supply agreement market response",
    )

    assert second.manifest == first.manifest
    assert second.manifest_path == first.manifest_path


def test_adaptive_retrieval_is_bounded_reproducible_and_auditable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2030, 1, 20, tzinfo=KST)
    records = [
        _record(
            index,
            trade_date=date(2030, 1, 10) - timedelta(days=index % 4),
            high_return_pct=15.0 if index % 2 else -2.0,
        )
        for index in range(1, 41)
    ]
    monkeypatch.setattr(BrainRecordStore, "list_records", lambda self: list(records))
    BrainRecordStore(tmp_path).rebuild_indexes()
    index = ProductionMemoryIndex(
        tmp_path,
        embedding_provider=_provider(),
        production=True,
    )
    snapshot = index.build(as_of=cutoff)
    import json

    cell_ids = [
        str(json.loads(line)["cell_id"])
        for line in (
            tmp_path / snapshot.cell_entries.artifact_path
        ).read_text(encoding="utf-8").splitlines()
    ]
    assert len(cell_ids) > 1
    initial_population = PopulationRetriever(tmp_path, memory_index=index).build(
        run_id="RUN-ADAPTIVE",
        cluster_id="EVT-ADAPTIVE",
        cutoff_at=cutoff,
        selected_cell_ids=[cell_ids[0]],
        independent_unit_type="issuer-day",
    )
    initial_representative = RepresentativeSelector(
        tmp_path,
        memory_index=index,
    ).build(
        population_manifest_path=initial_population.manifest_path,
        query="supply agreement mechanism",
    )
    adaptive = AdaptiveRetriever(tmp_path, memory_index=index)

    first, trace_path = adaptive.run(
        initial_population_manifest_path=initial_population.manifest_path,
        initial_representative_set_manifest_path=(
            initial_representative.manifest_path
        ),
        query="supply agreement mechanism",
        max_depth=2,
        max_cell_count=5,
        max_record_count=32,
        max_token_count=72_000,
    )
    original_search_cells = index.search_cells

    def fail_adaptive_search(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("verified adaptive resume must not repeat cell search")

    monkeypatch.setattr(index, "search_cells", fail_adaptive_search)
    second, second_path = adaptive.run(
        initial_population_manifest_path=initial_population.manifest_path,
        initial_representative_set_manifest_path=(
            initial_representative.manifest_path
        ),
        query="supply agreement mechanism",
        max_depth=2,
        max_cell_count=5,
        max_record_count=32,
        max_token_count=72_000,
    )
    monkeypatch.setattr(index, "search_cells", original_search_cells)

    assert first == second
    assert trace_path == second_path
    assert len(first.iterations) <= first.max_depth
    assert len(first.final_cell_ids) <= first.max_cell_count
    assert set(first.initial_cell_ids).issubset(first.final_cell_ids)
    assert adaptive.inspect(trace_path)["passed"] is True


def test_adaptive_inspection_detects_trace_tamper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index, population_path = _build_population(tmp_path, monkeypatch)
    representative = RepresentativeSelector(tmp_path, memory_index=index).build(
        population_manifest_path=population_path,
        query="supply agreement market response",
    )
    adaptive = AdaptiveRetriever(tmp_path, memory_index=index)
    _trace, trace_path = adaptive.run(
        initial_population_manifest_path=population_path,
        initial_representative_set_manifest_path=representative.manifest_path,
        query="supply agreement market response",
    )
    payload = read_json(trace_path)
    payload["stopped_reason"] = "TAMPERED"
    write_json(trace_path, payload)

    inspection = adaptive.inspect(trace_path)

    assert inspection["passed"] is False
    assert "adaptive_trace_recomputed_mismatch" in inspection["errors"]


def test_representative_and_adaptive_cli_commands_are_exposed() -> None:
    root_command = get_command(app)
    memory_command = root_command.commands["memory"]

    representative = memory_command.commands["build-representatives"]
    adaptive = memory_command.commands["adaptive-retrieve"]
    representative_options = {
        option
        for parameter in representative.params
        if hasattr(parameter, "opts")
        for option in parameter.opts
    }
    adaptive_options = {
        option
        for parameter in adaptive.params
        if hasattr(parameter, "opts")
        for option in parameter.opts
    }

    assert "--query" in representative_options
    assert "--min-information-gain" in adaptive_options


def test_representative_token_count_covers_final_serialization() -> None:
    candidate = _Candidate(
        record_id="REC-TOKEN",
        independent_unit_id="ISSUER_DAY:2030-01-10:000001",
        trade_date=date(2030, 1, 10),
        concentration_key="ISSUER:000001",
        strata=("outcome:SUCCESS_HIGH10",),
    )
    source = RepresentativeSourceRecord(
        record_id="REC-TOKEN",
        embedding=(1.0, 0.0),
        document="가" * 1_600,
        source_sha256=sha256_text("REC-TOKEN"),
        provenance_source_ids=("SRC-REC-TOKEN",),
    )

    row = _representative_row(
        candidate,
        source,
        rank=1,
        relevance=1.0,
        diversity=1.0,
        facility=1.0,
        distribution=1.0,
        selection_score=1.0,
        max_estimated_tokens=24_000,
    )

    assert row.estimated_token_count == conservative_token_upper_bound(
        canonical_json(row.model_dump(mode="json"))
    )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), True])
def test_representative_query_embedding_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    invalid: float | bool,
) -> None:
    index, population_path = _build_population(tmp_path, monkeypatch)
    monkeypatch.setattr(
        index.embedding_provider,
        "embed_texts",
        lambda texts: [[invalid, 0.0] for _text in texts],
    )

    with pytest.raises(ValueError, match="embedding must be non-empty and finite"):
        RepresentativeSelector(tmp_path, memory_index=index).build(
            population_manifest_path=population_path,
            query="supply agreement market response",
        )


def test_adaptive_expansion_query_depends_on_trigger() -> None:
    base = "supply agreement"

    polarity = _expansion_query(base, ["POLARITY_CONFLICT"])
    regime = _expansion_query(base, ["REGIME_DISAGREEMENT"])

    assert polarity != regime
    assert "negative near miss" in polarity
    assert "different market regime" in regime


def test_candidate_pool_is_unit_balanced_when_one_unit_has_many_records() -> None:
    candidates = [
        _Candidate(
            record_id=f"RICH-{index:05d}",
            independent_unit_id="UNIT-RICH",
            trade_date=date(2030, 1, 1),
            concentration_key="UNIT-RICH",
            strata=(f"lane:lane-{index:05d}",),
        )
        for index in range(1_000)
    ]
    candidates.extend(
        _Candidate(
            record_id=f"REC-{index:03d}",
            independent_unit_id=f"UNIT-{index:03d}",
            trade_date=date(2030, 1, 1),
            concentration_key=f"UNIT-{index:03d}",
            strata=("lane:shared",),
        )
        for index in range(100)
    )

    pool = _stratified_candidate_pool(candidates, "stable-corpus-root")

    assert len({candidate.independent_unit_id for candidate in pool}) == 101
    assert len(pool) <= 512


def test_distribution_constraint_overrides_relevance_bias() -> None:
    candidates = []
    source_by_id = {}
    population_strata = {
        "polarity:POSITIVE": set(),
        "polarity:NEGATIVE": set(),
    }
    for index in range(100):
        polarity = "POSITIVE" if index < 50 else "NEGATIVE"
        record_id = f"REC-DIST-{index:03d}"
        unit_id = f"UNIT-DIST-{index:03d}"
        candidate = _Candidate(
            record_id=record_id,
            independent_unit_id=unit_id,
            trade_date=date(2030, 1, 1) + timedelta(days=index % 4),
            concentration_key=unit_id,
            strata=(f"polarity:{polarity}",),
            record_label_quality="verified",
        )
        candidates.append(candidate)
        population_strata[f"polarity:{polarity}"].add(unit_id)
        source_by_id[record_id] = RepresentativeSourceRecord(
            record_id=record_id,
            embedding=(1.0, 0.0) if polarity == "POSITIVE" else (-1.0, 0.0),
            document=f"structural evidence {record_id}",
            source_sha256=sha256_text(record_id),
            provenance_source_ids=(f"SRC-{record_id}",),
        )

    selected = _mmr_select(
        candidates,
        source_by_id,
        [1.0, 0.0],
        target_selected_record_count=16,
        population_strata=population_strata,
    )
    counts = {
        polarity: sum(f"polarity:{polarity}" in row.strata for row in selected)
        for polarity in ("POSITIVE", "NEGATIVE")
    }

    assert len(selected) >= 16
    assert abs(counts["POSITIVE"] / len(selected) - 0.5) <= 0.25
    assert abs(counts["NEGATIVE"] / len(selected) - 0.5) <= 0.25


def test_quality_full_never_relaxes_distribution_tolerance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(diversity_module, "REPRESENTATIVE_MAX_SELECTED_RECORDS", 3)
    monkeypatch.setattr(
        diversity_module,
        "REPRESENTATIVE_DISTRIBUTION_SHARE_ERROR_TOLERANCE",
        0.0,
    )
    candidates = []
    source_by_id = {}
    population_strata = {
        "polarity:POSITIVE": set(),
        "polarity:NEGATIVE": set(),
    }
    for index in range(10):
        polarity = "POSITIVE" if index < 5 else "NEGATIVE"
        record_id = f"REC-SHORTFALL-{index:03d}"
        unit_id = f"UNIT-SHORTFALL-{index:03d}"
        candidate = _Candidate(
            record_id=record_id,
            independent_unit_id=unit_id,
            trade_date=date(2030, 1, 1) + timedelta(days=index % 8),
            concentration_key=unit_id,
            strata=(f"polarity:{polarity}",),
            record_label_quality="verified",
        )
        candidates.append(candidate)
        population_strata[f"polarity:{polarity}"].add(unit_id)
        source_by_id[record_id] = RepresentativeSourceRecord(
            record_id=record_id,
            embedding=(1.0, 0.0),
            document=f"structural evidence {record_id}",
            source_sha256=sha256_text(record_id),
            provenance_source_ids=(f"SRC-{record_id}",),
        )

    with pytest.raises(RepresentativeSelectionBudgetError, match="population distribution"):
        _mmr_select(
            candidates,
            source_by_id,
            [1.0, 0.0],
            target_selected_record_count=3,
            population_strata=population_strata,
        )

    with pytest.raises(RepresentativeSelectionBudgetError, match="population distribution"):
        _mmr_select(
            candidates,
            source_by_id,
            [1.0, 0.0],
            target_selected_record_count=3,
            population_strata=population_strata,
            max_token_count=48_000,
        )


def test_multi_record_unit_keeps_member_specific_minority_candidate() -> None:
    cutoff = datetime(2030, 1, 20, tzinfo=KST)
    unit_rows = [
        {
            "independent_unit_id": "ISSUER_DAY:2030-01-10:000001",
            "trade_date": "2030-01-10",
            "polarity": "CONFLICTING",
            "label_quality": "conflicting",
            "regime_clusters": ["RISK_ON"],
            "high_return_pct": 10.0,
        }
    ]
    member_rows = [
        {
            "record_id": "A-NORMAL",
            "independent_unit_id": "ISSUER_DAY:2030-01-10:000001",
            "record_type": "supervised_issuer_day_case",
            "label_quality": "verified",
            "path_type": "DIRECT",
            "memory_lanes": ["positive_analogs"],
        },
        {
            "record_id": "Z-COUNTER",
            "independent_unit_id": "ISSUER_DAY:2030-01-10:000001",
            "record_type": "counterexample",
            "label_quality": "missing",
            "path_type": "MARKET_MEMORY",
            "memory_lanes": ["counterexamples"],
        },
    ]

    candidates = _unit_candidates(member_rows, unit_rows, cutoff)

    by_id = {candidate.record_id: candidate for candidate in candidates}
    assert set(by_id) == {"A-NORMAL", "Z-COUNTER"}
    assert "role:COUNTEREXAMPLE" not in by_id["A-NORMAL"].strata
    assert "role:COUNTEREXAMPLE" in by_id["Z-COUNTER"].strata
    assert "record_quality:missing" in by_id["Z-COUNTER"].strata
