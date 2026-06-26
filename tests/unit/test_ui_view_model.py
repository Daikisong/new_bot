from __future__ import annotations

from datetime import date, datetime

from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    ContextManifest,
    DailyAnalysis,
    DominantSectorHypothesis,
    PathType,
    PriceSnapshot,
)
from news_scalping_lab.ui.app import _render_analysis, _render_candidate, _render_run_progress_summary
from news_scalping_lab.ui.view_model import (
    AnalysisViewModel,
    ArtifactLinks,
    CandidateEvidenceView,
    ExcludedButWatchView,
    SweepShardStatus,
    build_analysis_view_model,
)
from news_scalping_lab.utils import KST, write_json


class _FakeExpander:
    def __enter__(self) -> _FakeExpander:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeColumn:
    def __init__(self, parent: _FakeStreamlit) -> None:
        self.parent = parent

    def metric(self, label: str, value: object) -> None:
        self.parent.metrics.append((label, value))


class _FakeStreamlit:
    def __init__(self) -> None:
        self.writes: list[object] = []
        self.markdowns: list[str] = []
        self.captions: list[str] = []
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.subheaders: list[str] = []
        self.metrics: list[tuple[str, object]] = []
        self.expander_labels: list[str] = []
        self.downloads: list[tuple[str, str, bytes]] = []
        self.dataframes: list[list[dict[str, object]]] = []

    def columns(self, count: int) -> list[_FakeColumn]:
        return [_FakeColumn(self) for _ in range(count)]

    def metric(self, label: str, value: object) -> None:
        self.metrics.append((label, value))

    def subheader(self, value: str) -> None:
        self.subheaders.append(value)

    def markdown(self, value: str) -> None:
        self.markdowns.append(value)

    def write(self, value: object) -> None:
        self.writes.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def error(self, value: str) -> None:
        self.errors.append(value)

    def warning(self, value: str) -> None:
        self.warnings.append(value)

    def dataframe(
        self,
        value: list[dict[str, object]],
        *,
        hide_index: bool,
        use_container_width: bool,
    ) -> None:
        assert hide_index is True
        assert use_container_width is True
        self.dataframes.append(value)

    def expander(self, label: str, **_kwargs: object) -> _FakeExpander:
        self.expander_labels.append(label)
        return _FakeExpander()

    def download_button(self, label: str, *, data: bytes, file_name: str) -> None:
        self.downloads.append((label, file_name, data))


def test_build_analysis_view_model_groups_candidates_and_artifacts(tmp_path) -> None:
    trade_day = date(2030, 1, 10)
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    prediction = BlindPrediction(
        prediction_id="PRED-ui",
        trade_date=trade_day,
        cutoff_at=cutoff,
        created_at=cutoff,
        blind_analysis=BlindAnalysis(summary="UI test analysis."),
        dominant_sectors=[
            DominantSectorHypothesis(
                name="open-world cluster",
                formation_mechanism="current catalyst -> candidate paths",
                expected_breadth="narrow",
            )
        ],
        candidates=[
            Candidate(
                rank=2,
                ticker="UNKNOWN",
                company_name="BenefitCo",
                path_type=PathType.THEME_BENEFICIARY,
                thesis="Indirect candidate.",
                why_now="Needs beneficiary discovery.",
                causal_chain=["catalyst", "beneficiary"],
                inferred_evidence=["supply chain needs verification"],
                prior_negative_cases=["EP-negative"],
                novel_reasoning="New entity path can still be investigated.",
                counterarguments=["relation may be narrative only"],
                disconfirming_conditions=["no listed beneficiary found"],
                confidence_label=ConfidenceLabel.SPECULATIVE,
            ),
            Candidate(
                rank=1,
                ticker="UNKNOWN",
                company_name="DirectCo",
                path_type=PathType.SINGLE_EVENT,
                thesis="Direct candidate.",
                why_now="Direct mention.",
                causal_chain=["news", "direct"],
                direct_evidence=["direct pre-cutoff source"],
                market_memory_evidence=["D-1 absorption check"],
                prior_positive_cases=["EP-positive"],
                memory_episode_ids=["EP-positive", "EP-negative"],
                source_urls=["https://example.test/source"],
                confidence_label=ConfidenceLabel.LOW,
            ),
        ],
    )
    manifest = ContextManifest(
        run_id="RUN-ui",
        mode="exhaustive",
        trade_date=trade_day,
        cutoff_at=cutoff,
        as_of=cutoff,
        brain_version="brain-ui",
        accepted_episode_count=2,
        swept_episode_count=2,
        memory_sweep_shard_count=2,
        memory_sweep_cache_hits=1,
        memory_sweep_artifacts=[
            "runs/checkpoints/memory_sweep/RUN-ui/shard_0002.json",
            "runs/checkpoints/memory_sweep/RUN-ui/shard_0001.json",
            "runs/checkpoints/memory_sweep/RUN-ui/missing.json",
        ],
        source_ledger_artifact="runs/checkpoints/source_ledger/RUN-ui/source_ledger.jsonl",
        candidate_web_check_artifact=(
            "runs/checkpoints/candidate_web_checks/RUN-ui/candidate_web_checks.jsonl"
        ),
        candidate_verification_artifact=(
            "runs/checkpoints/candidate_verifications/RUN-ui/candidate_verification.json"
        ),
        final_synthesis_context_artifact=(
            "runs/checkpoints/final_synthesis_context/RUN-ui/final_synthesis_context.json"
        ),
        excluded_candidate_web_check_artifact=(
            "runs/checkpoints/candidate_web_checks/RUN-ui/excluded_candidate_web_checks.jsonl"
        ),
        price_snapshot=PriceSnapshot(
            source_name="mock",
            as_of=cutoff,
            allowed_through=date(2030, 1, 9),
        ),
    )
    sweep_dir = tmp_path / "runs" / "checkpoints" / "memory_sweep" / "RUN-ui"
    sweep_dir.mkdir(parents=True)
    write_json(
        sweep_dir / "shard_0001.json",
        {
            "schema_version": "nslab.memory_sweep_contribution.v1",
            "shard_index": 1,
            "episode_count": 1,
            "episode_ids": ["EP-1"],
            "from_cache": False,
        },
    )
    write_json(
        sweep_dir / "shard_0002.json",
        {
            "schema_version": "nslab.memory_sweep_contribution.v1",
            "shard_index": 2,
            "episode_count": 1,
            "episode_ids": ["EP-2"],
            "from_cache": True,
        },
    )
    analysis = DailyAnalysis(
        run_id="RUN-ui",
        trade_date=trade_day,
        cutoff_at=cutoff,
        created_at=cutoff,
        mode="exhaustive",
        blind_prediction=prediction,
        context_manifest=manifest,
        report_path="reports/2030-01-10_preopen.md",
        prediction_path="predictions/2030-01-10.json",
    )

    view = build_analysis_view_model(tmp_path, analysis)

    assert view.run_id == "RUN-ui"
    assert view.brain_version == "brain-ui"
    assert view.swept_episode_count == 2
    assert view.memory_sweep_cache_hits == 1
    assert [(shard.shard_index, shard.status) for shard in view.memory_sweep_shards] == [
        (1, "completed"),
        (2, "cached"),
        (None, "missing"),
    ]
    assert view.memory_sweep_shards[0].episode_ids == ["EP-1"]
    assert view.memory_sweep_shards[1].from_cache is True
    assert view.memory_sweep_shards[2].error == "artifact does not exist"
    assert view.dominant_sectors[0].name == "open-world cluster"
    assert [candidate.company_name for candidate in view.all_watchlist_candidates] == [
        "DirectCo",
        "BenefitCo",
    ]
    assert [item.candidate.company_name for item in view.excluded_but_watch] == ["BenefitCo"]
    assert view.excluded_but_watch[0].reasons == [
        "counterarguments: relation may be narrative only",
        "disconfirming_conditions: no listed beneficiary found",
        "prior_negative_cases: EP-negative",
    ]
    assert [candidate.company_name for candidate in view.candidates_by_path["SINGLE_EVENT"]] == [
        "DirectCo"
    ]
    direct_candidate = view.candidates_by_path["SINGLE_EVENT"][0]
    assert direct_candidate.path_type == "SINGLE_EVENT"
    assert direct_candidate.direct_evidence == ["direct pre-cutoff source"]
    assert direct_candidate.market_memory_evidence == ["D-1 absorption check"]
    assert direct_candidate.prior_positive_cases == ["EP-positive"]
    assert direct_candidate.memory_episode_ids == ["EP-positive", "EP-negative"]
    assert direct_candidate.source_urls == ["https://example.test/source"]
    assert [
        candidate.company_name for candidate in view.candidates_by_path["THEME_BENEFICIARY"]
    ] == ["BenefitCo"]
    beneficiary_candidate = view.candidates_by_path["THEME_BENEFICIARY"][0]
    assert beneficiary_candidate.inferred_evidence == ["supply chain needs verification"]
    assert beneficiary_candidate.prior_negative_cases == ["EP-negative"]
    assert beneficiary_candidate.novel_reasoning == "New entity path can still be investigated."
    assert beneficiary_candidate.counterarguments == ["relation may be narrative only"]
    assert beneficiary_candidate.disconfirming_conditions == ["no listed beneficiary found"]
    assert view.artifacts.prediction_json == tmp_path / "predictions" / "2030-01-10.json"
    assert view.artifacts.report_markdown == tmp_path / "reports" / "2030-01-10_preopen.md"
    assert view.artifacts.context_manifest_json == tmp_path / "runs" / "manifests" / "RUN-ui.json"
    assert (
        view.artifacts.source_ledger_jsonl
        == tmp_path / "runs" / "checkpoints" / "source_ledger" / "RUN-ui" / "source_ledger.jsonl"
    )
    assert view.artifacts.candidate_web_checks_jsonl == (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / "RUN-ui"
        / "candidate_web_checks.jsonl"
    )
    assert view.artifacts.candidate_verification_json == (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_verifications"
        / "RUN-ui"
        / "candidate_verification.json"
    )
    assert view.artifacts.final_synthesis_context_json == (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-ui"
        / "final_synthesis_context.json"
    )
    assert view.artifacts.excluded_candidate_web_checks_jsonl == (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / "RUN-ui"
        / "excluded_candidate_web_checks.jsonl"
    )


def test_render_analysis_exposes_required_dashboard_sections_and_downloads(tmp_path) -> None:
    fake_st = _FakeStreamlit()
    artifact_paths = {
        "prediction": tmp_path / "predictions" / "2030-01-10.json",
        "report": tmp_path / "reports" / "2030-01-10_preopen.md",
        "manifest": tmp_path / "runs" / "manifests" / "RUN-ui.json",
        "source_ledger": tmp_path
        / "runs"
        / "checkpoints"
        / "source_ledger"
        / "RUN-ui"
        / "source_ledger.jsonl",
        "web_checks": tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / "RUN-ui"
        / "candidate_web_checks.jsonl",
        "verification": tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_verifications"
        / "RUN-ui"
        / "candidate_verification.json",
        "final_context": tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-ui"
        / "final_synthesis_context.json",
        "excluded_checks": tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / "RUN-ui"
        / "excluded_candidate_web_checks.jsonl",
    }
    for label, path in artifact_paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(label.encode("utf-8"))

    candidates = [
        CandidateEvidenceView(
            rank=1,
            ticker="UNKNOWN",
            company_name="DirectCo",
            path_type=PathType.SINGLE_EVENT.value,
            thesis="Direct event candidate.",
            why_now="Pre-cutoff direct catalyst.",
            confidence_label="low",
            evidence_quality="medium",
            causal_chain=["news", "direct"],
            direct_evidence=["pre-cutoff article"],
            inferred_evidence=[],
            market_memory_evidence=[],
            prior_positive_cases=["EP-positive"],
            prior_negative_cases=[],
            novel_reasoning="Direct company event can matter without a static allowlist.",
            counterarguments=[],
            disconfirming_conditions=[],
            memory_episode_ids=["EP-positive"],
            source_urls=["https://example.test/direct"],
        ),
        CandidateEvidenceView(
            rank=2,
            ticker="UNKNOWN",
            company_name="BenefitCo",
            path_type=PathType.THEME_BENEFICIARY.value,
            thesis="Beneficiary discovery candidate.",
            why_now="Theme path needs company verification.",
            confidence_label="speculative",
            evidence_quality="low",
            causal_chain=["policy", "infrastructure", "beneficiary"],
            direct_evidence=[],
            inferred_evidence=["supply chain path"],
            market_memory_evidence=[],
            prior_positive_cases=[],
            prior_negative_cases=["EP-negative"],
            novel_reasoning="New beneficiary can be researched even when memory misses.",
            counterarguments=["relationship may be weak"],
            disconfirming_conditions=["not listed"],
            memory_episode_ids=["EP-negative"],
            source_urls=["https://example.test/beneficiary"],
        ),
        CandidateEvidenceView(
            rank=3,
            ticker="UNKNOWN",
            company_name="LeaderCo",
            path_type=PathType.CONTINUATION.value,
            thesis="Prior-leader continuation candidate.",
            why_now="D-1 market memory is still available.",
            confidence_label="medium",
            evidence_quality="medium",
            causal_chain=["D-1 leader", "continuation"],
            direct_evidence=[],
            inferred_evidence=[],
            market_memory_evidence=["D-1 price action only"],
            prior_positive_cases=["EP-continuation"],
            prior_negative_cases=[],
            novel_reasoning="Continuation is evaluated separately from direct news.",
            counterarguments=[],
            disconfirming_conditions=[],
            memory_episode_ids=["EP-continuation"],
            source_urls=["https://example.test/leader"],
        ),
    ]
    view = AnalysisViewModel(
        run_id="RUN-ui",
        mode="exhaustive",
        brain_version="brain-ui",
        accepted_episode_count=3,
        swept_episode_count=3,
        memory_sweep_shard_count=1,
        memory_sweep_cache_hits=0,
        memory_sweep_shards=[
            SweepShardStatus(
                shard_index=1,
                status="completed",
                episode_count=3,
                episode_ids=["EP-positive", "EP-negative", "EP-continuation"],
                from_cache=False,
                artifact_path=tmp_path / "runs" / "checkpoints" / "memory_sweep.json",
            )
        ],
        coverage_errors=[],
        dominant_sectors=[
            DominantSectorHypothesis(
                name="Open-world sector",
                formation_mechanism="news -> mechanism -> candidates",
                expected_breadth="selective",
                possible_leaders=["DirectCo", "LeaderCo"],
                supporting_cases=["EP-positive"],
                contradicting_cases=["EP-negative"],
                failure_conditions=["no verified beneficiary"],
            )
        ],
        all_watchlist_candidates=candidates,
        excluded_but_watch=[
            ExcludedButWatchView(
                candidate=candidates[1],
                reasons=["counterarguments: relationship may be weak"],
            )
        ],
        candidates_by_path={
            PathType.SINGLE_EVENT.value: [candidates[0]],
            PathType.THEME_BENEFICIARY.value: [candidates[1]],
            PathType.CONTINUATION.value: [candidates[2]],
            PathType.HYBRID.value: [],
        },
        artifacts=ArtifactLinks(
            prediction_json=artifact_paths["prediction"],
            report_markdown=artifact_paths["report"],
            context_manifest_json=artifact_paths["manifest"],
            source_ledger_jsonl=artifact_paths["source_ledger"],
            candidate_web_checks_jsonl=artifact_paths["web_checks"],
            candidate_verification_json=artifact_paths["verification"],
            final_synthesis_context_json=artifact_paths["final_context"],
            excluded_candidate_web_checks_jsonl=artifact_paths["excluded_checks"],
        ),
    )

    _render_analysis(view, fake_st)

    assert fake_st.metrics[:4] == [
        ("Mode", "exhaustive"),
        ("Brain", "brain-ui"),
        ("Swept episodes", "3/3"),
        ("Sweep shards", 1),
    ]
    assert "Memory sweep cache hits: 0" in fake_st.captions
    assert fake_st.subheaders == [
        "Memory Sweep Shards",
        "Dominant Sector Hypotheses",
        "All Pre-Open Watchlist Candidates",
        "Excluded But Watch",
        "Candidates",
        "Downloads",
    ]
    assert {
        "Open-world sector",
        "Single-news candidates",
        "Theme beneficiary candidates",
        "Prior-leader continuation candidates",
        "Hybrid candidates",
    }.issubset(set(fake_st.expander_labels))
    assert [label for label, _file_name, _data in fake_st.downloads] == [
        "Context manifest JSON",
        "Prediction JSON",
        "Pre-open report Markdown",
        "Source ledger JSONL",
        "Candidate web checks JSONL",
        "Candidate verification JSON",
        "Final synthesis context JSON",
        "Excluded candidate web checks JSONL",
    ]
    assert fake_st.warnings == []
    assert any(row.get("company") == "BenefitCo" for table in fake_st.dataframes for row in table)


def test_render_candidate_includes_full_evidence_and_objection_payload() -> None:
    fake_st = _FakeStreamlit()
    candidate = CandidateEvidenceView(
        rank=1,
        ticker="UNKNOWN",
        company_name="DetailCo",
        path_type="HYBRID",
        thesis="Detailed candidate.",
        why_now="Current catalyst.",
        confidence_label="medium",
        evidence_quality="low",
        causal_chain=["news", "mechanism"],
        direct_evidence=["direct source"],
        inferred_evidence=["inferred path"],
        market_memory_evidence=["market memory"],
        prior_positive_cases=["EP-positive"],
        prior_negative_cases=["EP-negative"],
        novel_reasoning="new company path",
        counterarguments=["weak relation"],
        disconfirming_conditions=["not listed"],
        memory_episode_ids=["EP-positive", "EP-negative"],
        source_urls=["https://example.test/source"],
    )

    _render_candidate(candidate, fake_st)

    detail_payload = next(item for item in fake_st.writes if isinstance(item, dict))
    assert detail_payload == {
        "why_now": "Current catalyst.",
        "causal_chain": ["news", "mechanism"],
        "direct_evidence": ["direct source"],
        "inferred_evidence": ["inferred path"],
        "market_memory_evidence": ["market memory"],
        "prior_positive_cases": ["EP-positive"],
        "prior_negative_cases": ["EP-negative"],
        "novel_reasoning": "new company path",
        "counterarguments": ["weak relation"],
        "disconfirming_conditions": ["not listed"],
        "memory_episode_ids": ["EP-positive", "EP-negative"],
        "source_urls": ["https://example.test/source"],
    }


def test_render_run_progress_summary_includes_coverage_and_shard_status(tmp_path) -> None:
    fake_st = _FakeStreamlit()
    view = AnalysisViewModel(
        run_id="RUN-progress",
        mode="exhaustive",
        brain_version="brain-progress",
        accepted_episode_count=3,
        swept_episode_count=2,
        memory_sweep_shard_count=2,
        memory_sweep_cache_hits=1,
        memory_sweep_shards=[
            SweepShardStatus(
                shard_index=1,
                status="completed",
                episode_count=1,
                episode_ids=["EP-1"],
                from_cache=False,
                artifact_path=tmp_path / "runs" / "shard_0001.json",
            ),
            SweepShardStatus(
                shard_index=2,
                status="cached",
                episode_count=1,
                episode_ids=["EP-2"],
                from_cache=True,
                artifact_path=tmp_path / "runs" / "shard_0002.json",
            ),
        ],
        coverage_errors=["exhaustive mode requires swept_episode_count == accepted_episode_count"],
        dominant_sectors=[],
        all_watchlist_candidates=[],
        excluded_but_watch=[],
        candidates_by_path={},
        artifacts=ArtifactLinks(
            prediction_json=tmp_path / "prediction.json",
            report_markdown=tmp_path / "report.md",
            context_manifest_json=tmp_path / "manifest.json",
        ),
    )

    _render_run_progress_summary(view, fake_st)

    assert fake_st.writes[0] == {
        "run_id": "RUN-progress",
        "mode": "exhaustive",
        "brain_version": "brain-progress",
        "memory_coverage": "2/3",
        "memory_sweep_shard_count": 2,
        "memory_sweep_cache_hits": 1,
    }
    assert fake_st.errors == [
        "exhaustive mode requires swept_episode_count == accepted_episode_count"
    ]
    assert fake_st.dataframes == [
        [
            {
                "shard": 1,
                "status": "completed",
                "episodes": 1,
                "from_cache": False,
                "episode_ids": "EP-1",
                "artifact": (tmp_path / "runs" / "shard_0001.json").as_posix(),
                "error": "",
            },
            {
                "shard": 2,
                "status": "cached",
                "episodes": 1,
                "from_cache": True,
                "episode_ids": "EP-2",
                "artifact": (tmp_path / "runs" / "shard_0002.json").as_posix(),
                "error": "",
            },
        ]
    ]
