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
from news_scalping_lab.ui.app import _render_candidate
from news_scalping_lab.ui.view_model import CandidateEvidenceView, build_analysis_view_model
from news_scalping_lab.utils import KST, write_json


class _FakeExpander:
    def __enter__(self) -> _FakeExpander:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _FakeStreamlit:
    def __init__(self) -> None:
        self.writes: list[object] = []
        self.markdowns: list[str] = []
        self.captions: list[str] = []

    def markdown(self, value: str) -> None:
        self.markdowns.append(value)

    def write(self, value: object) -> None:
        self.writes.append(value)

    def caption(self, value: str) -> None:
        self.captions.append(value)

    def expander(self, _label: str) -> _FakeExpander:
        return _FakeExpander()


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
