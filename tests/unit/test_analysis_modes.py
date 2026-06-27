from __future__ import annotations

import json
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

import pytest

from news_scalping_lab.brain.compiler import CATALOG_COMPILER_VERSION, BrainCompiler
from news_scalping_lab.cli import _inspect_context_manifest
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.context.assembler import ContextAssembler
from news_scalping_lab.context.episode_scope import inspect_manifest_episode_scope
from news_scalping_lab.context.modes import normalize_analysis_mode
from news_scalping_lab.context.session_pack import export_session_pack
from news_scalping_lab.context.sweep import MemorySweeper
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    ContextManifest,
    PathType,
    PriceSnapshot,
    ResearchEpisode,
)
from news_scalping_lab.inference.analyzer import (
    DailyAnalyzer,
    ExhaustiveCoverageError,
    _normalize_semantic_retrieval_category,
    _semantic_record_filters,
)
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import KST, canonical_json, read_json, sha256_text
from news_scalping_lab.warehouse import WarehouseStore


class OrderAssertingRetrieval:
    def __init__(self, first_pass_completed: Callable[[], bool]) -> None:
        self.first_pass_completed = first_pass_completed
        self.queries: list[str] = []

    def add_episode(self, episode: ResearchEpisode) -> None:
        raise AssertionError("test retrieval does not accept added episodes")

    def search_semantic(self, query: str, *, limit: int = 10) -> list[str]:
        assert self.first_pass_completed(), (
            "current-news first pass must finish before past semantic retrieval"
        )
        self.queries.append(query)
        return []

    def list_all_episodes(self) -> list[ResearchEpisode]:
        return []

    def get_available_as_of(self, cutoff_at: datetime) -> list[ResearchEpisode]:
        return []


class FirstPassTrackingAnalyzer(DailyAnalyzer):
    def __init__(
        self,
        settings: Settings,
        *,
        first_pass_state: dict[str, bool],
        retrieval: OrderAssertingRetrieval,
    ) -> None:
        self.first_pass_state = first_pass_state
        super().__init__(settings, retrieval=retrieval)

    def _infer_first_pass_mechanisms(self, news_texts: list[str]) -> list[str]:
        mechanisms = super()._infer_first_pass_mechanisms(news_texts)
        self.first_pass_state["completed"] = True
        return mechanisms


class EmptyRecordRetrieval:
    def search_semantic(self, query: str, *, limit: int = 10) -> list[str]:
        return []

    def search_records(
        self,
        query: str,
        *,
        limit: int = 10,
        **filters: object,
    ) -> list[str]:
        return []

    def list_all_episodes(self) -> list[ResearchEpisode]:
        return []

    def get_available_as_of(self, cutoff_at: datetime) -> list[ResearchEpisode]:
        return []


class SemanticCounterexampleRetrieval(EmptyRecordRetrieval):
    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        self.filters_seen: list[dict[str, object]] = []

    def search_records(
        self,
        query: str,
        *,
        limit: int = 10,
        **filters: object,
    ) -> list[str]:
        self.filters_seen.append(dict(filters))
        if filters.get("record_type") == "counterexample":
            return [self.record_id]
        return []


class SemanticPositiveRecordRetrieval(EmptyRecordRetrieval):
    def __init__(self, record_id: str) -> None:
        self.record_id = record_id
        self.filters_seen: list[dict[str, object]] = []

    def search_records(
        self,
        query: str,
        *,
        limit: int = 10,
        **filters: object,
    ) -> list[str]:
        self.filters_seen.append(dict(filters))
        if filters.get("training_eligible") is True:
            return [self.record_id]
        return []


def _brain_record(
    record_id: str,
    *,
    episode_id: str = "NSLAB-20300110-RECORDS",
    record_type: str = "supervised_direct_event_case",
    response_class: str = "positive_high10",
    available_from: datetime,
) -> BrainRecordEnvelope:
    trade_day = date(2030, 1, 9)
    payload = {
        "record_id": record_id,
        "record_type": record_type,
        "episode_id": episode_id,
        "trade_date": trade_day.isoformat(),
        "available_from": available_from.isoformat(),
        "training_target": "direct_event_response",
        "evidence_phase": "BLIND_SAFE",
        "ticker": "000001",
        "company_name": "Record Sweep Co",
        "path_type": "single_event",
        "response_class": response_class,
        "training_eligible": record_type != "counterexample",
        "provenance_source_ids": ["SRC-RECORD-SWEEP"],
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id=episode_id,
        trade_date=trade_day,
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="BLIND_SAFE",
        training_eligible=record_type != "counterexample",
        eligibility_reason="unit test record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-RECORD-SWEEP"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def _store_brain_records(tmp_path: Path, records: list[BrainRecordEnvelope]) -> None:
    episode_id = records[0].episode_id
    source_path = tmp_path / "synthetic_record_bundle.md"
    raw_payload = "\n".join(record.model_dump_json() for record in records)
    raw_sha = sha256_text(raw_payload)
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(tmp_path).store_bundle(
        source_path=source_path,
        envelope=ResearchBundleEnvelope(
            bundle_schema_version="nslab.research_bundle.v11",
            manifest_schema_version="nslab.bundle_manifest.v11",
            episode_schema_version="nslab.research_episode.v11",
            episode_id=episode_id,
            trade_date=records[0].trade_date,
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            available_from=min(record.available_from for record in records),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_bundle_sha256=raw_sha,
            raw_block_hashes={"brain_delta.jsonl": raw_sha},
            raw_block_counts={"brain_delta.jsonl": len(records)},
            provenance_closure_status="closed",
            adapter_name="unit-test",
            import_status="imported",
        ),
        index=NormalizedEpisodeIndex(
            episode_id=episode_id,
            trade_date=records[0].trade_date,
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            available_from=min(record.available_from for record in records),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
            record_ids=[record.record_id for record in records],
            record_count_by_type=dict.fromkeys(
                [record.record_type for record in records],
                1,
            ),
            training_eligible_record_count=sum(
                1 for record in records if record.training_eligible
            ),
            source_ids=["SRC-RECORD-SWEEP"],
        ),
        records=records,
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
    )


def test_normalize_analysis_mode_accepts_only_supported_modes() -> None:
    assert normalize_analysis_mode(" exhaustive ") == "exhaustive"
    assert normalize_analysis_mode("Brain") == "brain"
    assert normalize_analysis_mode("FAST") == "fast"

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        normalize_analysis_mode("retrieval")


def test_semantic_retrieval_categories_match_record_level_contract() -> None:
    assert _normalize_semantic_retrieval_category("negative_analogs") == (
        "negative_controls"
    )
    assert _normalize_semantic_retrieval_category("leader_selection_cases") == (
        "leader_selection_pairs"
    )
    assert _semantic_record_filters("negative_controls") == {
        "training_eligible": False
    }
    assert _semantic_record_filters("leader_selection_pairs") == {
        "record_type": "blind_leader_preference_pair"
    }


def test_context_assembler_rejects_unknown_analysis_mode(tmp_path) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        ContextAssembler(tmp_path).assemble(
            mode="typo",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            run_seed="seed",
        )


def test_context_run_id_changes_when_available_research_changes(tmp_path) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    assembler = ContextAssembler(tmp_path)

    before = assembler.assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
    )

    episode = ResearchEpisode(
        episode_id="EP-available",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Available lesson."),
        available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
    )
    store = ResearchStore(tmp_path)
    store.save_episode(episode)
    store.accept(episode.episode_id)

    after = ContextAssembler(tmp_path).assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
    )

    assert before.run_id != after.run_id
    assert before.accepted_episode_count == 0
    assert before.total_accepted_episode_count == 0
    assert before.total_accepted_episode_ids == []
    assert before.available_episode_count == 0
    assert before.unavailable_episode_count == 0
    assert before.unavailable_episode_ids == []
    assert after.accepted_episode_count == 1
    assert after.total_accepted_episode_count == 1
    assert after.total_accepted_episode_ids == ["EP-available"]
    assert after.available_episode_count == 1
    assert after.unavailable_episode_count == 0
    assert after.unavailable_episode_ids == []
    assert after.swept_episode_ids == ["EP-available"]

    future_episode = ResearchEpisode(
        episode_id="EP-future",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 10, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Future lesson."),
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    store.save_episode(future_episode)
    store.accept(future_episode.episode_id)

    with_unavailable = ContextAssembler(tmp_path).assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
    )

    assert with_unavailable.run_id == after.run_id
    assert with_unavailable.accepted_episode_count == 1
    assert with_unavailable.total_accepted_episode_count == 2
    assert with_unavailable.total_accepted_episode_ids == [
        "EP-available",
        "EP-future",
    ]
    assert with_unavailable.available_episode_count == 1
    assert with_unavailable.unavailable_episode_count == 1
    assert with_unavailable.unavailable_episode_ids == ["EP-future"]
    assert with_unavailable.swept_episode_ids == ["EP-available"]


def test_context_episode_scope_uses_manifest_episode_id_snapshot(
    tmp_path,
) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    store = ResearchStore(tmp_path)
    available = ResearchEpisode(
        episode_id="EP-available",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Available lesson."),
        available_from=datetime(2030, 1, 10, 8, 30, 0, tzinfo=KST),
    )
    later_postmortem = ResearchEpisode(
        episode_id="EP-later-postmortem",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        created_at=datetime(2030, 1, 10, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Later postmortem."),
        available_from=datetime(2030, 1, 11, 0, 0, 0, tzinfo=KST),
    )
    for episode in (available, later_postmortem):
        store.save_episode(episode)
        store.accept(episode.episode_id)

    scope = inspect_manifest_episode_scope(
        tmp_path,
        {
            "schema_version": "nslab.context_manifest.v1",
            "cutoff_at": cutoff_at.isoformat(),
            "accepted_episode_count": 1,
            "total_accepted_episode_count": 1,
            "total_accepted_episode_ids": ["EP-available"],
            "available_episode_count": 1,
            "unavailable_episode_count": 0,
            "unavailable_episode_ids": [],
        },
    )

    assert scope["passed"] is True
    assert scope["uses_total_accepted_episode_ids"] is True
    assert scope["current_total_accepted_episode_count"] == 2
    assert scope["expected_total_accepted_episode_count"] == 1
    assert scope["expected_total_accepted_episode_ids"] == ["EP-available"]


def test_context_assembler_filters_future_and_unknown_retrieved_episode_ids(
    tmp_path,
) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    store = ResearchStore(tmp_path)
    available = ResearchEpisode(
        episode_id="EP-available",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Available lesson."),
        available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
    )
    future = ResearchEpisode(
        episode_id="EP-future",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 10, 10, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Future lesson."),
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    for episode in (available, future):
        store.save_episode(episode)
        store.accept(episode.episode_id)

    manifest = ContextAssembler(tmp_path).assemble(
        mode="fast",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="same-news-and-model",
        retrieved_episode_ids=[
            "EP-available",
            "EP-future",
            "EP-missing",
            "EP-available",
        ],
    )

    assert manifest.retrieved_episode_ids == ["EP-available"]
    assert manifest.excluded_retrieved_episode_ids == ["EP-future", "EP-missing"]
    assert manifest.unavailable_episode_ids == ["EP-future"]


def test_context_assembler_uses_configurable_as_of_shard_episode_count(
    tmp_path,
) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    store = ResearchStore(tmp_path)
    for index in range(3):
        episode = ResearchEpisode(
            episode_id=f"EP-asof-shard-{index}",
            trade_date=date(2030, 1, 9),
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
            research_version="test",
            price_source_snapshot={"source": "test"},
            blind_analysis=BlindAnalysis(summary=f"As-of shard lesson {index}."),
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
        store.save_episode(episode)
        store.accept(episode.episode_id)

    manifest = ContextAssembler(tmp_path, shard_episode_count=1).assemble(
        mode="brain",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="as-of-shard-size",
    )

    assert len(manifest.shard_brain_files) == 3
    assert all("runs/checkpoints/brain_context/" in path for path in manifest.shard_brain_files)


def test_record_memory_sweep_outputs_required_retrieval_bundles(tmp_path) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-NEAR",
                response_class="near_miss_high5",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-COUNTER",
                record_type="counterexample",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-PAIR",
                record_type="blind_leader_preference_pair",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-THEME",
                record_type="supervised_theme_formation_case",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-CANDIDATE-ERROR",
                record_type="candidate_generation_error_case",
                response_class="negative",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
        ],
    )

    sweep = MemorySweeper(tmp_path, shard_episode_count=10).sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_id="RUN-record-bundles",
        current_news_texts=["new catalyst"],
        first_pass_mechanisms=["open-world first pass"],
        brain_version="brain-test",
    )

    assert sweep.errors == []
    assert sweep.record_artifact_paths
    payload = read_json(tmp_path / sweep.record_artifact_paths[0])
    assert [record["record_id"] for record in payload["near_misses"]] == [
        "BRAIN-NEAR"
    ]
    assert [record["record_id"] for record in payload["counterexamples"]] == [
        "BRAIN-COUNTER"
    ]
    assert [record["record_id"] for record in payload["leader_selection_pairs"]] == [
        "BRAIN-PAIR"
    ]
    assert [record["record_id"] for record in payload["theme_formation_failures"]] == [
        "BRAIN-THEME"
    ]
    assert [
        record["record_id"] for record in payload["candidate_generation_errors"]
    ] == ["BRAIN-CANDIDATE-ERROR"]


def test_record_memory_sweep_preserves_records_when_accepted_store_unreadable(
    tmp_path,
) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-SWEEP-AVAILABLE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-SWEEP-FUTURE",
                available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
            ),
        ],
    )
    accepted_path = (
        tmp_path / "research" / "accepted" / "NSLAB-20300110-RECORDS.json"
    )
    accepted_path.parent.mkdir(parents=True, exist_ok=True)
    accepted_path.write_text("{not valid json", encoding="utf-8")

    sweep = MemorySweeper(tmp_path, shard_episode_count=10).sweep(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_id="RUN-record-sweep-unreadable-accepted-store",
        current_news_texts=["new catalyst"],
        first_pass_mechanisms=["open-world first pass"],
        brain_version="brain-test",
    )

    assert sweep.accepted_episode_count == 0
    assert sweep.accepted_record_count == 2
    assert sweep.available_record_count == 1
    assert sweep.available_record_ids == ["BRAIN-SWEEP-AVAILABLE"]
    assert sweep.swept_record_ids == ["BRAIN-SWEEP-AVAILABLE"]
    assert sweep.record_artifact_paths
    payload = read_json(tmp_path / sweep.record_artifact_paths[0])
    assert payload["record_ids"] == ["BRAIN-SWEEP-AVAILABLE"]
    assert sweep.errors == ["accepted episode store is unreadable"]


@pytest.mark.asyncio
async def test_daily_analyzer_rejects_unknown_analysis_mode_before_writing_outputs(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        await DailyAnalyzer(settings).analyze(
            news_csv=tmp_path / "missing.csv",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="semi-fast",
            web_search=False,
        )

    assert list((tmp_path / "predictions").glob("*.json")) == []
    assert list((tmp_path / "runs" / "manifests").glob("*.json")) == []


@pytest.mark.asyncio
async def test_daily_analyzer_runs_current_news_first_pass_before_past_retrieval(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "first_pass_order_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","FirstPassCo, fresh catalyst",'
        '"The analyzer must read current news before searching past research."\n',
        encoding="utf-8",
    )
    first_pass_state = {"completed": False}
    retrieval = OrderAssertingRetrieval(lambda: first_pass_state["completed"])

    analysis = await FirstPassTrackingAnalyzer(
        settings,
        first_pass_state=first_pass_state,
        retrieval=retrieval,
    ).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="fast",
        web_search=False,
    )

    assert first_pass_state["completed"] is True
    assert retrieval.queries
    assert analysis.context_manifest.retrieved_episode_ids == []
    manifest = analysis.context_manifest
    assert manifest.open_world_first_analysis_artifact is not None
    assert manifest.open_world_first_analysis_sha256 is not None
    assert manifest.open_world_first_analysis_summary["mechanism_count"] >= 1
    assert "open_world_first_analysis" in manifest.prompt_hashes
    first_pass_payload = read_json(
        tmp_path / manifest.open_world_first_analysis_artifact
    )
    assert first_pass_payload["schema_version"] == "nslab.open_world_first_analysis.v1"
    assert first_pass_payload["run_id"] == manifest.run_id
    assert first_pass_payload["mechanisms"]
    assert first_pass_payload["beneficiary_investigation_questions"]
    assert first_pass_payload["uncertainties"]
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert synthesis_payload["open_world_first_analysis"] == first_pass_payload


def test_session_pack_rejects_unknown_analysis_mode_before_writing_pack(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)

    with pytest.raises(ValueError, match="analysis mode must be one of"):
        export_session_pack(
            settings,
            news_csv=tmp_path / "missing.csv",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
            mode="unknown",
        )

    assert not (tmp_path / "session_packs" / "2030-01-10").exists()


@pytest.mark.asyncio
async def test_fast_mode_keeps_open_world_candidates_when_retrieval_misses(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "fast_miss_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","UnseenFastCo, new catalyst",'
        '"No accepted research episode exists, so retrieval must miss without blocking."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="fast",
        web_search=False,
    )

    manifest = analysis.context_manifest
    prediction = analysis.blind_prediction
    assert manifest.mode == "fast"
    assert manifest.accepted_episode_count == 0
    assert manifest.swept_episode_count == 0
    assert manifest.retrieved_episode_ids == []
    assert prediction.candidates
    assert {candidate.path_type for candidate in prediction.candidates} >= {
        PathType.SINGLE_EVENT,
        PathType.THEME_BENEFICIARY,
        PathType.CONTINUATION,
    }
    beneficiary = next(
        candidate
        for candidate in prediction.candidates
        if candidate.path_type == PathType.THEME_BENEFICIARY
    )
    assert beneficiary.memory_episode_ids == []
    assert "memory has no exact precedent" in beneficiary.novel_reasoning
    assert "UnseenFastCo" in {candidate.company_name for candidate in prediction.candidates}


@pytest.mark.asyncio
async def test_retrieval_miss_does_not_block_open_world_candidates(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "open_world_retrieval_miss_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","NovelOpenWorldCo, new catalyst",'
        '"No record-level retrieval hit should be required before candidate generation."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        mode="fast",
        web_search=False,
    )

    manifest = analysis.context_manifest
    prediction = analysis.blind_prediction
    assert manifest.retrieved_episode_ids == []
    assert manifest.retrieved_record_ids == []
    assert manifest.semantic_retrieval_summary["record_retrieval_zero_is_valid"] is True
    assert prediction.candidates
    assert prediction.dominant_sectors
    assert "NovelOpenWorldCo" in {
        candidate.company_name for candidate in prediction.candidates
    }
    assert any(
        candidate.path_type == PathType.THEME_BENEFICIARY
        and candidate.company_name == "BENEFICIARY_DISCOVERY_REQUIRED"
        for candidate in prediction.candidates
    )


@pytest.mark.asyncio
async def test_exhaustive_mode_sweeps_available_brain_records(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-AVAILABLE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-FUTURE",
                available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "record_sweep_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","RecordSweepCo, new catalyst",'
        '"Exhaustive mode should sweep available brain records."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.accepted_record_count == 2
    assert manifest.available_record_count == 1
    assert manifest.available_record_ids == ["BRAIN-AVAILABLE"]
    assert manifest.training_eligible_available_record_ids == ["BRAIN-AVAILABLE"]
    assert manifest.swept_record_count == 1
    assert manifest.swept_record_ids == ["BRAIN-AVAILABLE"]
    assert manifest.retrieved_record_ids == ["BRAIN-AVAILABLE"]
    assert manifest.excluded_retrieved_record_ids == ["BRAIN-FUTURE"]
    assert manifest.semantic_retrieval_record_ids == ["BRAIN-AVAILABLE"]
    assert manifest.excluded_semantic_retrieval_record_ids == ["BRAIN-FUTURE"]
    assert manifest.compiler_mode == "asof_context"
    assert manifest.brain_compiler_provider == "deterministic_catalog"
    assert manifest.brain_compiler_model == CATALOG_COMPILER_VERSION
    assert manifest.brain_compiler_catalog_only is False
    assert manifest.record_sweep_artifacts
    assert manifest.record_sweep_shard_count == 1
    assert manifest.errors == []
    record_sweep_payload = read_json(tmp_path / manifest.record_sweep_artifacts[0])
    assert record_sweep_payload["record_ids"] == ["BRAIN-AVAILABLE"]
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert synthesis_payload["accepted_record_count"] == 2
    assert synthesis_payload["available_record_count"] == 1
    assert synthesis_payload["training_eligible_available_record_count"] == 1
    assert synthesis_payload["swept_record_count"] == 1
    assert synthesis_payload["swept_record_ids"] == ["BRAIN-AVAILABLE"]
    assert synthesis_payload["retrieved_record_ids"] == ["BRAIN-AVAILABLE"]
    assert synthesis_payload["excluded_retrieved_record_ids"] == ["BRAIN-FUTURE"]
    assert synthesis_payload["available_record_ids"] == ["BRAIN-AVAILABLE"]
    assert synthesis_payload["training_eligible_available_record_ids"] == [
        "BRAIN-AVAILABLE"
    ]
    assert synthesis_payload["semantic_retrieval_record_ids"] == ["BRAIN-AVAILABLE"]
    assert synthesis_payload["excluded_semantic_retrieval_record_ids"] == [
        "BRAIN-FUTURE"
    ]
    semantic_context = synthesis_payload["additional_semantic_retrieval"]
    assert semantic_context["included_record_ids"] == ["BRAIN-AVAILABLE"]
    assert semantic_context["excluded_record_ids"] == ["BRAIN-FUTURE"]
    assert [record["record_id"] for record in semantic_context["records"]] == [
        "BRAIN-AVAILABLE"
    ]
    assert synthesis_payload["brain_compiler"] == {
        "mode": "asof_context",
        "provider": "deterministic_catalog",
        "model": CATALOG_COMPILER_VERSION,
        "catalog_only": False,
    }
    assert synthesis_payload["record_level_shard_contributions"][0]["payload"][
        "record_ids"
    ] == ["BRAIN-AVAILABLE"]
    assert synthesis_payload["record_sweep_artifacts"] == manifest.record_sweep_artifacts
    assert (
        synthesis_payload["record_sweep_artifact_hashes"]
        == manifest.record_sweep_artifact_hashes
    )
    assert synthesis_payload["record_sweep_shard_count"] == 1
    assert synthesis_payload["record_sweep_cache_hits"] == 0
    semantic_rows = [
        json.loads(line)
        for line in (tmp_path / str(manifest.semantic_retrieval_artifact))
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    positive_row = next(
        row for row in semantic_rows if row["category"] == "positive_analogs"
    )
    assert positive_row["record_retrieval_filters"] == {"training_eligible": True}
    assert positive_row["included_record_ids"] == ["BRAIN-AVAILABLE"]
    assert positive_row["excluded_record_ids"] == ["BRAIN-FUTURE"]
    candidate_error_row = next(
        row for row in semantic_rows if row["category"] == "candidate_generation_errors"
    )
    assert candidate_error_row["record_retrieval_filters"] == {
        "record_type": [
            "candidate_generation_error_case",
            "candidate_ranking_error_case",
            "entity_resolution_error_case",
            "ranking_error_case",
            "row_disposition_error_case",
        ]
    }
    assert candidate_error_row["included_record_ids"] == []
    assert candidate_error_row["excluded_record_ids"] == []
    assert (
        manifest.semantic_retrieval_summary["category_query_counts"][
            "candidate_generation_errors"
        ]
        == 1
    )
    assert "candidate_generation_errors" in manifest.semantic_retrieval_summary[
        "required_categories"
    ]
    assert manifest.semantic_retrieval_summary["included_record_count"] == 1
    assert manifest.semantic_retrieval_summary["excluded_record_count"] == 1
    assert manifest.semantic_retrieval_summary["record_retrieval_zero_is_valid"] is True
    saved_manifest_path = tmp_path / "runs" / "manifests" / f"{manifest.run_id}.json"
    inspection = _inspect_context_manifest(
        tmp_path,
        saved_manifest_path,
        read_json(saved_manifest_path),
    )
    record_sweep = inspection["record_sweep"]
    assert record_sweep["passed"] is True
    assert record_sweep["hashes_verified"] is True
    assert record_sweep["metadata_verified"] is True
    assert record_sweep["source_hashes_verified"] is True
    assert record_sweep["shard_count_verified"] is True
    assert record_sweep["cache_hits_verified"] is True
    assert record_sweep["swept_record_ids_verified"] is True
    assert record_sweep["observed_record_ids"] == ["BRAIN-AVAILABLE"]


def test_context_assembler_preserves_record_context_when_accepted_store_unreadable(
    tmp_path,
) -> None:
    ensure_project_dirs(Settings(project_root=tmp_path))
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-CONTEXT-AVAILABLE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-CONTEXT-FUTURE",
                available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
            ),
        ],
    )
    accepted_path = (
        tmp_path / "research" / "accepted" / "NSLAB-20300110-RECORDS.json"
    )
    accepted_path.parent.mkdir(parents=True, exist_ok=True)
    accepted_path.write_text("{not valid json", encoding="utf-8")

    manifest = ContextAssembler(tmp_path).assemble(
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        run_seed="record-context-unreadable-accepted-store",
        retrieved_record_ids=[
            "BRAIN-CONTEXT-AVAILABLE",
            "BRAIN-CONTEXT-FUTURE",
        ],
    )

    assert manifest.accepted_episode_count == 0
    assert manifest.total_accepted_episode_count == 0
    assert manifest.accepted_record_count == 2
    assert manifest.available_record_ids == ["BRAIN-CONTEXT-AVAILABLE"]
    assert manifest.swept_record_ids == ["BRAIN-CONTEXT-AVAILABLE"]
    assert manifest.retrieved_record_ids == ["BRAIN-CONTEXT-AVAILABLE"]
    assert manifest.excluded_retrieved_record_ids == ["BRAIN-CONTEXT-FUTURE"]
    assert "accepted episode store is unreadable" in manifest.errors
    assert "swept_record_count must equal available_record_count" not in manifest.errors


@pytest.mark.asyncio
async def test_daily_analyzer_preserves_record_sweep_when_accepted_store_unreadable(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-ANALYZE-AVAILABLE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-ANALYZE-FUTURE",
                available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
            ),
        ],
    )
    accepted_path = (
        tmp_path / "research" / "accepted" / "NSLAB-20300110-RECORDS.json"
    )
    accepted_path.parent.mkdir(parents=True, exist_ok=True)
    accepted_path.write_text("{not valid json", encoding="utf-8")
    news_csv = tmp_path / "record_backed_corrupt_accepted_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","RecordBackedCo, new catalyst",'
        '"Analysis should preserve record sweep evidence when accepted JSON is corrupt."\n',
        encoding="utf-8",
    )

    with pytest.raises(ExhaustiveCoverageError):
        await DailyAnalyzer(settings).analyze(
            news_csv=news_csv,
            trade_date=date(2030, 1, 10),
            cutoff_at=cutoff_at,
            mode="exhaustive",
            web_search=False,
        )

    manifest_paths = list((tmp_path / "runs" / "manifests").glob("*.json"))
    assert len(manifest_paths) == 1
    manifest = read_json(manifest_paths[0])
    assert manifest["errors"] == ["accepted episode store is unreadable"]
    assert manifest["accepted_record_count"] == 2
    assert manifest["available_record_ids"] == ["BRAIN-ANALYZE-AVAILABLE"]
    assert manifest["swept_record_ids"] == ["BRAIN-ANALYZE-AVAILABLE"]
    assert manifest["excluded_retrieved_record_ids"] == ["BRAIN-ANALYZE-FUTURE"]
    assert manifest["record_sweep_artifacts"]
    record_sweep = read_json(tmp_path / manifest["record_sweep_artifacts"][0])
    assert record_sweep["record_ids"] == ["BRAIN-ANALYZE-AVAILABLE"]


@pytest.mark.asyncio
async def test_exhaustive_record_coverage_100_percent(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-COVERED-DIRECT",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-COVERED-COUNTER",
                record_type="counterexample",
                response_class="negative_control",
                available_from=datetime(2030, 1, 10, 8, 30, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "coverage_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","CoverageCo, new catalyst",'
        '"Every available brain record must be swept exactly once."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    expected_ids = ["BRAIN-COVERED-COUNTER", "BRAIN-COVERED-DIRECT"]
    assert manifest.accepted_record_count == 2
    assert manifest.available_record_count == 2
    assert manifest.swept_record_count == manifest.available_record_count
    assert manifest.available_record_ids == expected_ids
    assert manifest.swept_record_ids == expected_ids
    assert set(manifest.available_record_ids) - set(manifest.swept_record_ids) == set()
    assert manifest.errors == []


def test_exhaustive_coverage_rejects_record_id_mismatch(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = ContextManifest(
        run_id="RUN-EXHAUSTIVE-ID-MISMATCH",
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        as_of=cutoff_at,
        accepted_episode_count=1,
        swept_episode_count=1,
        available_record_count=1,
        available_record_ids=["BRAIN-AVAILABLE"],
        swept_record_count=1,
        swept_record_ids=["BRAIN-SWEPT"],
        price_snapshot=PriceSnapshot(
            source_name="test",
            allowed_through=date(2030, 1, 9),
        ),
    )

    with pytest.raises(ExhaustiveCoverageError):
        DailyAnalyzer(settings)._fail_if_exhaustive_coverage_incomplete(manifest)

    assert manifest.errors == [
        "exhaustive mode requires swept_record_ids to match available_record_ids"
    ]
    saved_manifest = read_json(
        tmp_path / "runs" / "manifests" / "RUN-EXHAUSTIVE-ID-MISMATCH.json"
    )
    assert saved_manifest["available_record_ids"] == ["BRAIN-AVAILABLE"]
    assert saved_manifest["swept_record_ids"] == ["BRAIN-SWEPT"]
    assert saved_manifest["errors"] == manifest.errors


def test_exhaustive_coverage_rejects_episode_id_mismatch(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    manifest = ContextManifest(
        run_id="RUN-EXHAUSTIVE-EPISODE-ID-MISMATCH",
        mode="exhaustive",
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        as_of=cutoff_at,
        accepted_episode_count=1,
        total_accepted_episode_count=1,
        total_accepted_episode_ids=["EP-AVAILABLE"],
        available_episode_count=1,
        unavailable_episode_count=0,
        unavailable_episode_ids=[],
        swept_episode_count=1,
        swept_episode_ids=["EP-SWEPT"],
        price_snapshot=PriceSnapshot(
            source_name="test",
            allowed_through=date(2030, 1, 9),
        ),
    )

    with pytest.raises(ExhaustiveCoverageError):
        DailyAnalyzer(settings)._fail_if_exhaustive_coverage_incomplete(manifest)

    assert manifest.errors == [
        "exhaustive mode requires swept_episode_ids to match available accepted episode ids"
    ]
    saved_manifest = read_json(
        tmp_path / "runs" / "manifests" / "RUN-EXHAUSTIVE-EPISODE-ID-MISMATCH.json"
    )
    assert saved_manifest["total_accepted_episode_ids"] == ["EP-AVAILABLE"]
    assert saved_manifest["swept_episode_ids"] == ["EP-SWEPT"]
    assert saved_manifest["errors"] == manifest.errors


def test_record_level_available_from_filter(tmp_path) -> None:
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-ASOF-AVAILABLE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-ASOF-FUTURE",
                record_type="counterexample",
                response_class="negative_control",
                available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
            ),
        ],
    )
    WarehouseStore(tmp_path).rebuild_all()

    rows = WarehouseStore(tmp_path).query_brain_records(
        available_from_as_of=cutoff_at.isoformat(),
        limit=10,
    )

    assert [row["record_id"] for row in rows] == ["BRAIN-ASOF-AVAILABLE"]
    assert rows[0]["available_from"] == "2030-01-10T08:00:00+09:00"


@pytest.mark.asyncio
async def test_no_future_record_in_historical_context(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-HISTORICAL-AVAILABLE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-HISTORICAL-FUTURE",
                available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "historical_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","HistoricalCo, new catalyst",'
        '"Future available_from records must not enter historical context."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert manifest.available_record_ids == ["BRAIN-HISTORICAL-AVAILABLE"]
    assert manifest.retrieved_record_ids == ["BRAIN-HISTORICAL-AVAILABLE"]
    assert manifest.excluded_retrieved_record_ids == ["BRAIN-HISTORICAL-FUTURE"]
    assert synthesis_payload["available_record_ids"] == ["BRAIN-HISTORICAL-AVAILABLE"]
    assert synthesis_payload["retrieved_record_ids"] == ["BRAIN-HISTORICAL-AVAILABLE"]
    assert synthesis_payload["excluded_retrieved_record_ids"] == [
        "BRAIN-HISTORICAL-FUTURE"
    ]
    assert "BRAIN-HISTORICAL-FUTURE" not in json.dumps(
        synthesis_payload["record_level_shard_contributions"],
        ensure_ascii=False,
    )


@pytest.mark.asyncio
async def test_negative_and_counterexample_retrieval(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-NEG-POSITIVE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-NEG-COUNTER",
                record_type="counterexample",
                response_class="negative_control",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-NEG-ERROR",
                record_type="candidate_generation_error_case",
                response_class="missed_direct_candidate",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "negative_counterexample_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","NegativeCounterCo, new catalyst",'
        '"Semantic retrieval must include positive, error, and counterexample records."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    semantic_rows = [
        json.loads(line)
        for line in (tmp_path / str(manifest.semantic_retrieval_artifact))
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    counterexample_row = next(
        row for row in semantic_rows if row["category"] == "counterexamples"
    )
    error_row = next(
        row for row in semantic_rows if row["category"] == "candidate_generation_errors"
    )
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]

    assert manifest.counterexample_record_ids == ["BRAIN-NEG-COUNTER"]
    assert counterexample_row["included_record_ids"] == ["BRAIN-NEG-COUNTER"]
    assert error_row["included_record_ids"] == ["BRAIN-NEG-ERROR"]
    assert synthesis_payload["negative_record_ids"] == ["BRAIN-NEG-COUNTER"]
    assert synthesis_payload["counterexample_record_ids"] == ["BRAIN-NEG-COUNTER"]
    assert [record["record_id"] for record in synthesis_payload["counterexample_records"]] == [
        "BRAIN-NEG-COUNTER"
    ]
    assert all(
        candidate.prior_negative_record_ids == ["BRAIN-NEG-COUNTER"]
        for candidate in analysis.blind_prediction.candidates
    )


@pytest.mark.asyncio
async def test_counterexample_record_ids_reach_prediction_outputs(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-POSITIVE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
            _brain_record(
                "BRAIN-COUNTER",
                record_type="counterexample",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "counterexample_record_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","CounterRecordCo, new catalyst",'
        '"Counterexample records should reach final outputs."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="exhaustive",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.counterexample_record_ids == ["BRAIN-COUNTER"]
    assert analysis.blind_prediction.candidates
    assert all(
        candidate.prior_negative_record_ids == ["BRAIN-COUNTER"]
        for candidate in analysis.blind_prediction.candidates
    )
    assert all(
        "BRAIN-COUNTER" in candidate.memory_record_ids
        for candidate in analysis.blind_prediction.candidates
    )
    assert all(
        "BRAIN-COUNTER" not in candidate.prior_positive_record_ids
        for candidate in analysis.blind_prediction.candidates
    )
    assert analysis.blind_prediction.dominant_sectors
    assert all(
        sector.contradicting_record_ids == ["BRAIN-COUNTER"]
        for sector in analysis.blind_prediction.dominant_sectors
    )
    saved_prediction = read_json(tmp_path / analysis.prediction_path)
    assert saved_prediction["candidates"][0]["prior_negative_record_ids"] == [
        "BRAIN-COUNTER"
    ]
    assert saved_prediction["dominant_sectors"][0]["contradicting_record_ids"] == [
        "BRAIN-COUNTER"
    ]
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert synthesis_payload["counterexample_record_ids"] == ["BRAIN-COUNTER"]
    assert synthesis_payload["negative_record_ids"] == ["BRAIN-COUNTER"]
    report = (tmp_path / analysis.report_path).read_text(encoding="utf-8")
    assert "Prior negative record IDs: BRAIN-COUNTER" in report
    assert "Contradicting record IDs: BRAIN-COUNTER" in report


@pytest.mark.asyncio
async def test_fast_mode_does_not_auto_load_unretrieved_counterexamples(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-FAST-UNRETRIEVED-COUNTER",
                record_type="counterexample",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "fast_unretrieved_counterexample_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","FastModeCo, new catalyst",'
        '"Fast mode should not auto-load unrelated counterexample records."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(
        settings,
        retrieval=EmptyRecordRetrieval(),
    ).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="fast",
        web_search=False,
    )

    manifest = analysis.context_manifest
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert manifest.available_record_ids == ["BRAIN-FAST-UNRETRIEVED-COUNTER"]
    assert manifest.swept_record_ids == []
    assert manifest.retrieved_record_ids == []
    assert manifest.semantic_retrieval_record_ids == []
    assert manifest.counterexample_record_ids == []
    assert synthesis_payload["counterexample_record_ids"] == []
    assert synthesis_payload["counterexample_records"] == []
    assert synthesis_payload["negative_record_ids"] == []
    assert all(
        candidate.prior_negative_record_ids == []
        for candidate in analysis.blind_prediction.candidates
    )


@pytest.mark.asyncio
async def test_fast_mode_semantic_counterexamples_reach_final_context(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-FAST-SEMANTIC-COUNTER",
                record_type="counterexample",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "fast_semantic_counterexample_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","SemanticCounterCo, new catalyst",'
        '"Semantic retrieval should carry counterexample records to synthesis."\n',
        encoding="utf-8",
    )
    retrieval = SemanticCounterexampleRetrieval("BRAIN-FAST-SEMANTIC-COUNTER")

    analysis = await DailyAnalyzer(settings, retrieval=retrieval).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="fast",
        web_search=False,
    )

    manifest = analysis.context_manifest
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert {"record_type": "counterexample"} in retrieval.filters_seen
    assert manifest.retrieved_record_ids == []
    assert manifest.semantic_retrieval_record_ids == ["BRAIN-FAST-SEMANTIC-COUNTER"]
    assert manifest.counterexample_record_ids == ["BRAIN-FAST-SEMANTIC-COUNTER"]
    assert synthesis_payload["counterexample_record_ids"] == [
        "BRAIN-FAST-SEMANTIC-COUNTER"
    ]
    assert [
        record["record_id"] for record in synthesis_payload["counterexample_records"]
    ] == ["BRAIN-FAST-SEMANTIC-COUNTER"]
    assert synthesis_payload["negative_record_ids"] == [
        "BRAIN-FAST-SEMANTIC-COUNTER"
    ]
    assert all(
        candidate.prior_negative_record_ids == ["BRAIN-FAST-SEMANTIC-COUNTER"]
        for candidate in analysis.blind_prediction.candidates
    )


@pytest.mark.asyncio
async def test_fast_mode_semantic_positive_records_reach_prediction_memory(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    _store_brain_records(
        tmp_path,
        [
            _brain_record(
                "BRAIN-FAST-SEMANTIC-POSITIVE",
                available_from=datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST),
            ),
        ],
    )
    BrainCompiler(tmp_path).rebuild(mode="full")
    news_csv = tmp_path / "fast_semantic_positive_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","SemanticPositiveCo, new catalyst",'
        '"Semantic retrieval should carry positive records into prediction memory."\n',
        encoding="utf-8",
    )
    retrieval = SemanticPositiveRecordRetrieval("BRAIN-FAST-SEMANTIC-POSITIVE")

    analysis = await DailyAnalyzer(settings, retrieval=retrieval).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="fast",
        web_search=False,
    )

    manifest = analysis.context_manifest
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert {"training_eligible": True} in retrieval.filters_seen
    assert manifest.retrieved_record_ids == []
    assert manifest.semantic_retrieval_record_ids == ["BRAIN-FAST-SEMANTIC-POSITIVE"]
    assert manifest.counterexample_record_ids == []
    assert synthesis_payload["positive_record_ids"] == [
        "BRAIN-FAST-SEMANTIC-POSITIVE"
    ]
    assert synthesis_payload["negative_record_ids"] == []
    assert [record["record_id"] for record in synthesis_payload["retrieved_records"]] == [
        "BRAIN-FAST-SEMANTIC-POSITIVE"
    ]
    assert [
        record["record_id"]
        for record in synthesis_payload["additional_semantic_retrieval"]["records"]
    ] == ["BRAIN-FAST-SEMANTIC-POSITIVE"]
    assert all(
        candidate.prior_positive_record_ids == ["BRAIN-FAST-SEMANTIC-POSITIVE"]
        for candidate in analysis.blind_prediction.candidates
    )
    assert all(
        candidate.prior_negative_record_ids == []
        for candidate in analysis.blind_prediction.candidates
    )


@pytest.mark.asyncio
async def test_brain_mode_keeps_shard_brain_context_and_sweeps_available_episodes(
    tmp_path,
) -> None:
    settings = Settings(project_root=tmp_path)
    ensure_project_dirs(settings)
    store = ResearchStore(tmp_path)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    for index in range(2):
        episode = ResearchEpisode(
            episode_id=f"EP-brain-mode-{index}",
            trade_date=date(2030, 1, 9),
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
            research_version="brain-mode-test",
            price_source_snapshot={"source": "test"},
            blind_analysis=BlindAnalysis(summary=f"Brain-mode lesson {index}."),
            available_from=datetime(2030, 1, 10, 0, 0, 0, tzinfo=KST),
        )
        store.save_episode(episode)
        store.accept(episode.episode_id)
    BrainCompiler(
        tmp_path,
        shard_episode_count=settings.limits.shard_episode_count,
    ).rebuild(mode="full")
    news_csv = tmp_path / "brain_mode_news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","BrainModeCo, new catalyst",'
        '"Brain mode should load shard brain context and keep coverage visible."\n',
        encoding="utf-8",
    )

    analysis = await DailyAnalyzer(settings).analyze(
        news_csv=news_csv,
        trade_date=date(2030, 1, 10),
        cutoff_at=cutoff_at,
        mode="brain",
        web_search=False,
    )

    manifest = analysis.context_manifest
    assert manifest.mode == "brain"
    assert manifest.accepted_episode_count == 2
    assert manifest.swept_episode_count == 2
    assert manifest.swept_episode_ids == ["EP-brain-mode-0", "EP-brain-mode-1"]
    assert manifest.compiler_mode == "full"
    assert manifest.brain_compiler_provider == "deterministic_catalog"
    assert manifest.brain_compiler_model == CATALOG_COMPILER_VERSION
    assert manifest.brain_compiler_catalog_only is True
    assert manifest.memory_sweep_artifacts
    assert manifest.shard_brain_files
    assert manifest.shard_brain_file_hashes
    assert manifest.errors == []
    sweep_payloads = [
        read_json(tmp_path / relative_path)
        for relative_path in manifest.memory_sweep_artifacts
    ]
    assert [payload["episode_ids"] for payload in sweep_payloads] == [
        ["EP-brain-mode-0", "EP-brain-mode-1"]
    ]
    assert all(payload["mode"] == "brain" for payload in sweep_payloads)
    assert all((tmp_path / relative_path).exists() for relative_path in manifest.shard_brain_files)
    synthesis_payload = read_json(
        tmp_path / str(manifest.final_synthesis_context_artifact)
    )["payload"]
    assert synthesis_payload["brain_compiler"] == {
        "mode": "full",
        "provider": "deterministic_catalog",
        "model": CATALOG_COMPILER_VERSION,
        "catalog_only": True,
    }
