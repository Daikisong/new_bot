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
from news_scalping_lab.ui.view_model import build_analysis_view_model
from news_scalping_lab.utils import KST, write_json


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
                confidence_label=ConfidenceLabel.LOW,
            ),
        ],
    )
    manifest = ContextManifest(
        run_id="RUN-ui",
        mode="exhaustive",
        trade_date=trade_day,
        cutoff_at=cutoff,
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
        price_snapshot=PriceSnapshot(source_name="mock", allowed_through=date(2030, 1, 9)),
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
    assert [candidate.company_name for candidate in view.candidates_by_path["SINGLE_EVENT"]] == [
        "DirectCo"
    ]
    assert [
        candidate.company_name for candidate in view.candidates_by_path["THEME_BENEFICIARY"]
    ] == ["BenefitCo"]
    assert view.artifacts.prediction_json == tmp_path / "predictions" / "2030-01-10.json"
    assert view.artifacts.report_markdown == tmp_path / "reports" / "2030-01-10_preopen.md"
    assert view.artifacts.context_manifest_json == tmp_path / "runs" / "manifests" / "RUN-ui.json"
