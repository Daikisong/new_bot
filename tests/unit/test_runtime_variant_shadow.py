from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import news_scalping_lab.evaluation.runtime_variant_shadow as runtime_variant_shadow_module
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import (
    BlindAnalysis,
    BlindPrediction,
    Candidate,
    ConfidenceLabel,
    PathType,
)
from news_scalping_lab.evaluation.runtime_variant_shadow import (
    QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    _aggregate_micro_market_metrics,
    _build_report,
    _expected_calibration_error,
    _final_memory_citation_rate,
    _load_canonical_outcome_universe,
    _materialize_case_news,
    _precision_at,
    _prediction_metrics,
    _recall_at,
    _runtime_counter_delta,
    _runtime_counter_snapshot,
    _runtime_trace_paths,
    _score_paired_shadow_case,
    _validate_shadow_progress_closure,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.utils import (
    KST,
    file_sha256,
    parse_datetime,
    sha256_bytes,
    write_json,
)


class _CounterLLM:
    def __init__(self) -> None:
        self.live = 0
        self.cache = 0

    def identity(self) -> dict[str, int]:
        return {
            "live_agent_call_count": self.live,
            "cache_hit_count": self.cache,
        }


class _CounterEmbedding:
    embedding_query_count = 0
    embedding_text_count = 0
    embedding_input_char_count = 0


_REAL_QUALITY_EVAL_ROOT = (
    Path(__file__).resolve().parents[3]
    / "nslab_semantic_upgrade_v7_eval_v2"
    / "project"
)


def _outcome_row(
    ticker: str,
    *,
    rank: int | None,
    high_return: float | None,
    code_only: bool = False,
    eligible: bool = True,
) -> dict[str, object]:
    row: dict[str, object] = {
        "outcome_row_id": f"OUT-{ticker}",
        "snapshot_date": "2030-01-10",
        "data_quality_status": "clean" if eligible else "blocked_by_corporate_action",
        "label_quality": "verified" if eligible else "quarantined",
        "quarantined": not eligible,
        "tradable": eligible,
        "high_return_pct": high_return,
        "high_return_rank": rank,
        "upper_limit_touched": bool(high_return is not None and high_return >= 29.0),
        "upper_limit_closed": False,
        "upper_limit_released": False,
        "corporate_action_warning": not eligible,
        "new_listing_or_no_reference": False,
    }
    row["code" if code_only else "ticker"] = ticker
    return row


def _write_outcomes(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "outcome_ledger.jsonl"
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    return path


def _prediction(tickers: list[str]) -> BlindPrediction:
    observed_at = datetime(2030, 1, 10, 8, 59, 0, tzinfo=KST)
    return BlindPrediction(
        prediction_id="PRED-market-contract",
        trade_date=date(2030, 1, 10),
        cutoff_at=observed_at,
        created_at=observed_at,
        blind_analysis=BlindAnalysis(summary="sealed"),
        candidates=[
            Candidate(
                rank=rank,
                ticker=ticker,
                company_name=ticker,
                path_type=PathType.SINGLE_EVENT,
                thesis="sealed thesis",
                why_now="sealed evidence",
                confidence_label=(
                    ConfidenceLabel.HIGH if rank == 1 else ConfidenceLabel.LOW
                ),
            )
            for rank, ticker in enumerate(tickers, start=1)
        ],
    )


def _shadow_progress_seal(case_id: str, variant_id: str) -> dict[str, object]:
    variant_digit = "0" if variant_id == "V0" else "1"
    return {
        "schema_version": "nslab.runtime_variant_prediction_seal.v1",
        "case_id": case_id,
        "variant_id": variant_id,
        "news_sha256": "a" * 64,
        "prediction": {
            "artifact_path": f"predictions/{case_id}-{variant_id}.json",
            "sha256": variant_digit * 64,
        },
        "context_manifest": {
            "artifact_path": f"runs/manifests/{case_id}-{variant_id}.json",
            "sha256": ("2" if variant_id == "V0" else "3") * 64,
        },
        "outcome_reference_count": 0,
    }


def _shadow_progress_observation(
    case_id: str,
    variant_id: str,
) -> dict[str, object]:
    seal = _shadow_progress_seal(case_id, variant_id)
    prediction = seal["prediction"]
    context = seal["context_manifest"]
    assert isinstance(prediction, dict)
    assert isinstance(context, dict)
    return {
        "case_id": case_id,
        "variant_id": variant_id,
        "news_sha256": seal["news_sha256"],
        "prediction_sha256": prediction["sha256"],
        "context_manifest_sha256": context["sha256"],
        "truth_sha256": "f" * 64,
    }


def test_market_metric_helpers_use_rank_cutoffs_and_calibration_bins() -> None:
    ranked = ["A", "B", "C", "D"]
    assert _recall_at(ranked, {"B", "D"}, 2) == 0.5
    assert _precision_at(ranked, {"B", "D"}, 2) == 0.5
    assert _expected_calibration_error(
        [
            {
                "metrics": {
                    "calibration_pairs": [
                        {"probability": 0.9, "outcome": 1.0},
                        {"probability": 0.1, "outcome": 0.0},
                    ]
                }
            }
        ]
    ) == pytest.approx(0.1)
    assert (
        _aggregate_micro_market_metrics(
            [
                {
                    "metrics": {
                        "upper_limit_target_count": 1,
                        "high20_target_count": 2,
                        "high10_target_count": 3,
                        "upper_limit_hit_count_at_5": 1,
                        "high20_hit_count_at_5": 1,
                        "high10_hit_count_at_5": 2,
                        "selected_count_at_5": 5,
                    }
                }
            ]
        )["micro_high20_recall_at_5"]
        == 0.5
    )


def test_outcome_adapter_accepts_code_only_and_excludes_null_quarantine(
    tmp_path: Path,
) -> None:
    path = _write_outcomes(
        tmp_path,
        [
            _outcome_row("000001", rank=1, high_return=30.0, code_only=True),
            _outcome_row("000002", rank=2, high_return=10.0, code_only=True),
            _outcome_row(
                "000003",
                rank=None,
                high_return=None,
                code_only=True,
                eligible=False,
            ),
        ],
    )

    outcome = _load_canonical_outcome_universe(
        path,
        trade_date=date(2030, 1, 10),
    )

    assert outcome.raw_tickers == ("000001", "000002", "000003")
    assert outcome.tickers == ("000001", "000002")
    assert outcome.excluded_tickers == ("000003",)
    assert outcome.leader_ticker == "000001"


@pytest.mark.skipif(
    not _REAL_QUALITY_EVAL_ROOT.is_dir(),
    reason="local sealed QUALITY_FULL evaluation project is unavailable",
)
def test_actual_three_quality_ledgers_match_canonical_audit_counts() -> None:
    cases = (
        ("NSLAB-20260102-be50ec83", date(2026, 1, 2), 2672, "008355", 14, 38, 161),
        ("NSLAB-20260324-bc14e7ac", date(2026, 3, 24), 2656, "065530", 19, 56, 183),
        ("NSLAB-20260316-5e3b82bc", date(2026, 3, 16), 2662, "085620", 12, 42, 123),
    )
    for episode_id, trade_day, size, leader, upper, high20, high10 in cases:
        outcome = _load_canonical_outcome_universe(
            _REAL_QUALITY_EVAL_ROOT
            / "research"
            / "episodes"
            / episode_id
            / "raw_blocks"
            / "outcome_ledger.jsonl",
            trade_date=trade_day,
        )
        assert (
            len(outcome.tickers),
            outcome.leader_ticker,
            len(outcome.upper_limit_tickers),
            len(outcome.high20_tickers),
            len(outcome.high10_tickers),
        ) == (size, leader, upper, high20, high10)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        (
            [
                {
                    **_outcome_row("000001", rank=1, high_return=30.0),
                    "code": "000002",
                }
            ],
            "ticker/code mismatch",
        ),
        (
            [
                _outcome_row("000001", rank=1, high_return=30.0),
                {
                    **_outcome_row("000001", rank=2, high_return=20.0),
                    "outcome_row_id": "OUT-DIFFERENT",
                },
            ],
            "duplicate canonical ticker",
        ),
        (
            [
                _outcome_row("000001", rank=1, high_return=30.0),
                {
                    **_outcome_row("000002", rank=2, high_return=20.0),
                    "outcome_row_id": "OUT-000001",
                },
            ],
            "duplicate outcome ID",
        ),
        (
            [_outcome_row("12345", rank=1, high_return=30.0)],
            "six-digit ticker",
        ),
    ],
)
def test_outcome_adapter_fails_closed_on_alias_duplicate_and_format_drift(
    tmp_path: Path,
    rows: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _load_canonical_outcome_universe(
            _write_outcomes(tmp_path, rows),
            trade_date=date(2030, 1, 10),
        )


def test_market_metrics_separate_exact_leader_from_top_pick_high20(
    tmp_path: Path,
) -> None:
    path = _write_outcomes(
        tmp_path,
        [
            _outcome_row("000001", rank=1, high_return=30.0),
            _outcome_row("000002", rank=2, high_return=25.0),
            _outcome_row(
                "000003",
                rank=None,
                high_return=None,
                eligible=False,
            ),
        ],
    )

    metrics = _prediction_metrics(
        _prediction(["000002", "000003", "000001"]),
        path,
        evaluation_universe_tickers=["000001", "000002", "000003"],
        probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    )

    assert metrics["leader_truth_ticker"] == "000001"
    assert metrics["leader_selection_accuracy"] == 0.0
    assert metrics["top_pick_high20_hit"] == 1.0
    assert metrics["leader_rank"] == 3
    assert metrics["leader_mrr"] == pytest.approx(1 / 3)
    assert metrics["outcome_ineligible_selected_count_at_5"] == 1
    assert metrics["population_count"] == 2
    assert metrics["evaluation_universe_count"] == 3


def test_market_metrics_use_fixed_k_and_none_for_empty_targets(
    tmp_path: Path,
) -> None:
    path = _write_outcomes(
        tmp_path,
        [
            _outcome_row("000001", rank=1, high_return=5.0),
            _outcome_row("000002", rank=2, high_return=2.0),
        ],
    )

    metrics = _prediction_metrics(
        _prediction(["000001"]),
        path,
        evaluation_universe_tickers=["000001", "000002"],
        probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    )

    assert metrics["high20_recall_at_5"] is None
    assert metrics["high20_precision_at_5"] == 0.0
    assert metrics["selected_count_at_5"] == 5
    assert metrics["actual_selected_count_at_5"] == 1
    assert metrics["high20_no_positive_false_positive_count_at_5"] == 1


def test_population_calibration_uses_same_full_eligible_universe(
    tmp_path: Path,
) -> None:
    path = _write_outcomes(
        tmp_path,
        [
            _outcome_row("000001", rank=1, high_return=30.0),
            _outcome_row("000002", rank=2, high_return=15.0),
            _outcome_row("000003", rank=3, high_return=1.0),
        ],
    )
    universe = ["000001", "000002", "000003"]

    left = _prediction_metrics(
        _prediction(["000001"]),
        path,
        evaluation_universe_tickers=universe,
        probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    )
    right = _prediction_metrics(
        _prediction(["000002", "000003"]),
        path,
        evaluation_universe_tickers=universe,
        probability_policy_version=QUALITY_HIGH20_PROBABILITY_POLICY_VERSION,
    )

    assert left["population_universe_sha256"] == right["population_universe_sha256"]
    assert left["population_count"] == right["population_count"] == 3
    assert sum(row["count"] for row in left["population_calibration_bins"]) == 3
    assert sum(row["count"] for row in right["population_calibration_bins"]) == 3
    assert left["population_brier"] != right["population_brier"]


def test_empty_prediction_has_no_citation_rate() -> None:
    assert _final_memory_citation_rate(_prediction([])) is None


def test_shadow_news_reconstruction_uses_one_verified_buffer_per_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source_ledger.jsonl"
    source.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "available_before_cutoff": True,
                        "published_at_kst": "2026-01-02T08:30:00",
                        "title": "cutoff-safe title",
                        "body": "cutoff-safe body",
                    }
                ),
                json.dumps(
                    {
                        "available_before_cutoff": False,
                        "published_at_kst": "2026-01-02T09:01:00",
                        "title": "future title",
                        "body": "future body",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    index = tmp_path / "normalized_episode_index.json"
    write_json(index, {"cutoff_at": "2026-01-02T08:59:59+09:00"})
    case = {
        "episode_id": "EP-shadow",
        "source_ledger": {
            "artifact_path": source.as_posix(),
            "sha256": file_sha256(source),
        },
        "normalized_index": {
            "artifact_path": index.as_posix(),
            "sha256": file_sha256(index),
        },
    }
    source_path = source.resolve()
    index_path = index.resolve()
    read_counts = {source_path: 0, index_path: 0}
    original_read_bytes = Path.read_bytes

    def swapping_read_bytes(path: Path) -> bytes:
        resolved = path.resolve()
        if resolved in read_counts:
            read_counts[resolved] += 1
            if read_counts[resolved] > 1:
                return b'{"coordinated_swap":true}\n'
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", swapping_read_bytes)

    path, _digest, cutoff = _materialize_case_news(
        tmp_path,
        output_dir=tmp_path / "output",
        case=case,
    )

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert cutoff == "2026-01-02T08:59:59+09:00"
    assert read_counts == {source_path: 1, index_path: 1}
    assert rows == [
        {
            "date": "2026-01-02",
            "time": "08:30:00",
            "title": "cutoff-safe title",
            "body": "cutoff-safe body",
        }
    ]

    monkeypatch.setattr(Path, "read_bytes", original_read_bytes)
    path.write_bytes(b"date,time,title,body\r\n2026-01-02,08:30:00,tampered,body\r\n")
    with pytest.raises(ValueError, match="immutable artifact drifted"):
        _materialize_case_news(
            tmp_path,
            output_dir=tmp_path / "output",
            case=case,
        )


def test_shadow_preloaded_news_rejects_a_swapped_materialized_file(
    tmp_path: Path,
) -> None:
    news_path = tmp_path / "news.csv"
    news_path.write_text(
        "date,time,title,body\n2026-01-02,08:30:00,sealed title,sealed body\n",
        encoding="utf-8-sig",
    )
    trade_date = date(2026, 1, 2)
    preloaded = load_news_csv(news_path, trade_date=trade_date)
    analyzer = object.__new__(DailyAnalyzer)

    observed = analyzer._validate_preloaded_news_batch(
        news_csv=news_path,
        trade_date=trade_date,
        batch=preloaded,
    )
    assert observed is preloaded

    news_path.write_text(
        "date,time,title,body\n2026-01-02,08:30:00,swapped title,swapped body\n",
        encoding="utf-8-sig",
    )
    with pytest.raises(ValueError, match="hash differs"):
        analyzer._validate_preloaded_news_batch(
            news_csv=news_path,
            trade_date=trade_date,
            batch=preloaded,
        )


@pytest.mark.asyncio
async def test_shadow_preload_does_not_relax_quality_full_all_or_none(
    tmp_path: Path,
) -> None:
    news_path = tmp_path / "news.csv"
    news_path.write_text(
        "date,time,title,body\n2026-01-02,08:30:00,sealed title,sealed body\n",
        encoding="utf-8-sig",
    )
    trade_date = date(2026, 1, 2)
    preloaded = load_news_csv(news_path, trade_date=trade_date)
    analyzer = object.__new__(DailyAnalyzer)
    analyzer.settings = Settings(project_root=tmp_path)
    analyzer._sealed_d_minus_one_only = False

    with pytest.raises(ValueError, match="must be injected together"):
        await analyzer.analyze(
            news_csv=news_path,
            trade_date=trade_date,
            cutoff_at=datetime(2026, 1, 2, 8, 59, 59, tzinfo=KST),
            preloaded_news_batch=preloaded,
        )


def test_shadow_outcome_is_untouched_without_complete_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolved_references: list[object] = []

    def record_artifact_path(root: Path, reference: object) -> Path:
        resolved_references.append(reference)
        return root / "should-not-be-opened"

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_artifact_path",
        record_artifact_path,
    )
    case = {
        "episode_id": "EP-shadow",
        "trade_date": "2026-01-02",
        "outcome_ledger": {
            "artifact_path": "outcome.jsonl",
            "sha256": "a" * 64,
        },
    }

    with pytest.raises(ValueError, match="paired prediction seal"):
        _score_paired_shadow_case(
            tmp_path,
            case=case,
            prediction_seals=[
                {
                    "schema_version": "nslab.runtime_variant_prediction_seal.v1",
                    "case_id": "EP-shadow",
                    "variant_id": "V0",
                    "news_sha256": "b" * 64,
                    "outcome_reference_count": 0,
                }
            ],
            expected_news_sha256="b" * 64,
            memory_snapshot_id="MEM-test",
        )

    assert resolved_references == []


def test_shadow_progress_rejects_scored_prefix_before_global_seal_closure() -> None:
    seals = [
        _shadow_progress_seal("EP-A", variant_id)
        for variant_id in ("V0", "V1")
    ]
    observations = [
        _shadow_progress_observation("EP-A", variant_id)
        for variant_id in ("V0", "V1")
    ]
    progress = {
        "identity": {"case_ids": ["EP-A", "EP-B"]},
        "prediction_seals": seals,
        "completed_prediction_seal_count": len(seals),
        "observations": observations,
        "completed_observation_count": len(observations),
    }

    with pytest.raises(ValueError, match="predates global prediction seal closure"):
        _validate_shadow_progress_closure(progress)


def test_shadow_progress_allows_full_seals_with_paired_scored_prefix() -> None:
    seals = [
        _shadow_progress_seal(case_id, variant_id)
        for case_id in ("EP-A", "EP-B")
        for variant_id in ("V0", "V1")
    ]
    observations = [
        _shadow_progress_observation("EP-A", variant_id)
        for variant_id in ("V0", "V1")
    ]
    progress = {
        "identity": {"case_ids": ["EP-A", "EP-B"]},
        "prediction_seals": seals,
        "completed_prediction_seal_count": len(seals),
        "observations": observations,
        "completed_observation_count": len(observations),
    }

    _validate_shadow_progress_closure(progress)


def test_shadow_trace_or_snapshot_failure_leaves_outcome_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cutoff = datetime(2026, 1, 2, 8, 59, 59, tzinfo=KST)
    prediction = _prediction(["000001"]).model_copy(
        update={
            "trade_date": date(2026, 1, 2),
            "cutoff_at": cutoff,
            "context_manifest_id": "RUN-shadow",
        }
    )

    def loaded_seal(
        root: Path,
        *,
        seal: dict[str, object],
    ) -> tuple[BlindPrediction, object, str, str]:
        variant_id = str(seal["variant_id"])
        manifest = SimpleNamespace(
            run_id="RUN-shadow",
            trade_date=date(2026, 1, 2),
            cutoff_at=cutoff,
            news_sha256="b" * 64,
            llm_model_config={
                "runtime_retrieval_variant": (
                    "legacy" if variant_id == "V0" else "v4"
                )
            },
            blind_price_repository_access_count=0,
            blind_current_price_access_count=0,
            blind_web_search_call_count=0,
            external_web_evidence_count=0,
            no_d_outcome_exposed=True,
            event_clustering_result_sha256="c" * 64,
            open_world_first_analysis_sha256="d" * 64,
            news_novelty_review_sha256="e" * 64,
        )
        return prediction, manifest, "1" * 64, "2" * 64

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_load_shadow_prediction_seal",
        loaded_seal,
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_trace_stats",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("snapshot closure failed")
        ),
    )
    touched_references: list[object] = []

    def record_artifact_path(root: Path, reference: object) -> Path:
        touched_references.append(reference)
        return root / "outcome.jsonl"

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_artifact_path",
        record_artifact_path,
    )
    seals = [
        {
            **_shadow_progress_seal("EP-shadow", variant_id),
            "news_sha256": "b" * 64,
            "trade_date": "2026-01-02",
            "cutoff_at": cutoff.isoformat(),
        }
        for variant_id in ("V0", "V1")
    ]

    with pytest.raises(ValueError, match="snapshot closure failed"):
        _score_paired_shadow_case(
            tmp_path,
            case={
                "episode_id": "EP-shadow",
                "trade_date": "2026-01-02",
                "outcome_ledger": {
                    "artifact_path": "outcome.jsonl",
                    "sha256": "f" * 64,
                },
            },
            prediction_seals=seals,
            expected_news_sha256="b" * 64,
            memory_snapshot_id="MEM-test",
        )

    assert touched_references == []


def test_shadow_pair_scores_both_variants_from_one_verified_outcome_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truth_bytes = b'{"sealed":"outcome"}\n'
    truth_path = tmp_path / "outcome.jsonl"
    truth_path.write_bytes(truth_bytes)
    truth_sha256 = sha256_bytes(truth_bytes)
    case = {
        "episode_id": "EP-shadow",
        "trade_date": "2026-01-02",
        "split": "CALIBRATION",
        "outcome_ledger": {
            "artifact_path": truth_path.as_posix(),
            "sha256": truth_sha256,
        },
    }
    seals = {
        variant_id: {
            "schema_version": "nslab.runtime_variant_prediction_seal.v1",
            "case_id": "EP-shadow",
            "variant_id": variant_id,
            "news_sha256": "b" * 64,
            "elapsed_seconds": 1.0,
            "runtime_metrics": {},
            "outcome_reference_count": 0,
        }
        for variant_id in ("V0", "V1")
    }
    loaded = {
        variant_id: (object(), object(), "c" * 64, "d" * 64, {})
        for variant_id in ("V0", "V1")
    }
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_verify_paired_shadow_prediction_closure",
        lambda *args, **kwargs: (seals, loaded),
    )
    observed_buffers: list[bytes] = []

    def record_observation(**kwargs: object) -> dict[str, object]:
        payload = kwargs["truth_bytes"]
        assert isinstance(payload, bytes)
        observed_buffers.append(payload)
        return {
            "case_id": "EP-shadow",
            "variant_id": kwargs["variant_id"],
            "truth_sha256": kwargs["truth_sha256"],
        }

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_observation",
        record_observation,
    )
    original_open = Path.open
    truth_open_count = 0

    def coordinated_swap_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> object:
        nonlocal truth_open_count
        if path.resolve() == truth_path.resolve() and "r" in mode:
            truth_open_count += 1
            payload = truth_bytes if truth_open_count == 1 else b"tampered"
            return (
                io.BytesIO(payload)
                if "b" in mode
                else io.StringIO(payload.decode("utf-8"))
            )
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", coordinated_swap_open)

    observations = _score_paired_shadow_case(
        tmp_path,
        case=case,
        prediction_seals=list(seals.values()),
        expected_news_sha256="b" * 64,
        memory_snapshot_id="MEM-test",
    )

    assert truth_open_count == 1
    assert len(observed_buffers) == 2
    assert observed_buffers[0] is observed_buffers[1]
    assert all(row["truth_sha256"] == truth_sha256 for row in observations)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "resume_mode",
    ["partial_seals", "full_seals_paired_prefix"],
)
async def test_shadow_resume_seals_every_case_before_any_outcome_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    resume_mode: str,
) -> None:
    cases = [
        {
            "episode_id": case_id,
            "trade_date": "2026-01-02",
            "split": "CALIBRATION",
            "outcome_ledger": {
                "artifact_path": f"outcomes/{case_id}.jsonl",
                "sha256": "f" * 64,
            },
        }
        for case_id in ("EP-A", "EP-B")
    ]
    events: list[str] = []
    injected_batch_ids: list[int] = []

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_read_selection_with_sha256",
        lambda path: (
            {
                "schema_version": "nslab.semantic_upgrade_split_selection.v1",
                "cases": cases,
            },
            "1" * 64,
        ),
    )

    def read_static_json(path: Path) -> tuple[object, str]:
        if path.name == "brain_manifest.json":
            return {
                "build_mode": "llm-full",
                "production_memory_snapshot_id": "MEM-test",
            }, "2" * 64
        return {"coverage_scope": "EVALUATION_REPLAY_BUILD"}, "3" * 64

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_read_json_file_once",
        read_static_json,
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "active_memory_snapshot_manifest",
        lambda root: SimpleNamespace(snapshot_id="MEM-test", evaluation_only=True),
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "create_llm_provider",
        lambda settings: object(),
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "create_configured_embedding_provider",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "LocalRetrievalStore",
        lambda *args, **kwargs: object(),
    )

    class FakeAnalyzer:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.variant = str(kwargs["runtime_retrieval_variant"])

        async def analyze(self, **kwargs: object) -> object:
            injected_batch_ids.append(id(kwargs["shadow_preloaded_news_batch"]))
            events.append(f"analyze:{self.variant}")
            return object()

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "DailyAnalyzer",
        FakeAnalyzer,
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_materialize_case_news",
        lambda root, *, output_dir, case: (
            tmp_path / f"{case['episode_id']}.csv",
            f"{case['episode_id']}-news",
            "2026-01-02T08:59:59+09:00",
        ),
    )

    def fake_load_news_csv(path: Path, *, trade_date: date) -> object:
        return SimpleNamespace(sha256=f"{path.stem}-news")

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "load_news_csv",
        fake_load_news_csv,
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_runtime_counter_snapshot",
        lambda *args, **kwargs: {},
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_runtime_counter_delta",
        lambda *args, **kwargs: {},
    )

    def fake_seal(
        root: Path,
        *,
        case_id: str,
        variant_id: str,
        news_sha256: str,
        **kwargs: object,
    ) -> dict[str, object]:
        events.append(f"seal:{case_id}:{variant_id}")
        return {
            "schema_version": "nslab.runtime_variant_prediction_seal.v1",
            "case_id": case_id,
            "variant_id": variant_id,
            "news_sha256": news_sha256,
            "outcome_reference_count": 0,
        }

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_seal_shadow_prediction",
        fake_seal,
    )

    def fake_load_progress(
        path: Path,
        *,
        identity: dict[str, object],
        run_id: str,
    ) -> dict[str, object]:
        existing_case_ids = (
            ("EP-A", "EP-B")
            if resume_mode == "full_seals_paired_prefix"
            else ("EP-A",)
        )
        existing = [
            {
                "schema_version": "nslab.runtime_variant_prediction_seal.v1",
                "case_id": case_id,
                "variant_id": variant_id,
                "news_sha256": f"{case_id}-news",
                "outcome_reference_count": 0,
            }
            for case_id in existing_case_ids
            for variant_id in ("V0", "V1")
        ]
        observations = (
            [
                {
                    "case_id": "EP-A",
                    "variant_id": variant_id,
                    "truth_sha256": "f" * 64,
                }
                for variant_id in ("V0", "V1")
            ]
            if resume_mode == "full_seals_paired_prefix"
            else []
        )
        progress = {
            "schema_version": runtime_variant_shadow_module.RUNTIME_VARIANT_SHADOW_VERSION,
            "run_id": run_id,
            "identity": identity,
            "prediction_seals": existing,
            "completed_prediction_seal_count": len(existing),
            "observations": observations,
            "completed_observation_count": len(observations),
        }
        write_json(path, progress)
        return progress

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_load_progress",
        fake_load_progress,
    )

    def fake_verify(*args: object, **kwargs: object) -> tuple[dict[str, object], dict[str, object]]:
        case = kwargs["case"]
        assert isinstance(case, dict)
        events.append(f"verify:{case['episode_id']}")
        return {}, {}

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_verify_paired_shadow_prediction_closure",
        fake_verify,
    )

    def fake_score(*args: object, **kwargs: object) -> list[dict[str, object]]:
        case = kwargs["case"]
        assert isinstance(case, dict)
        case_id = str(case["episode_id"])
        progress_paths = list(tmp_path.rglob("progress.json"))
        assert len(progress_paths) == 1
        persisted = json.loads(progress_paths[0].read_text(encoding="utf-8"))
        persisted_keys = {
            (str(row["case_id"]), str(row["variant_id"]))
            for row in persisted["prediction_seals"]
        }
        assert persisted_keys == {
            (expected_case_id, variant_id)
            for expected_case_id in ("EP-A", "EP-B")
            for variant_id in ("V0", "V1")
        }
        events.append(f"outcome:{case_id}")
        return [
            {"case_id": case_id, "variant_id": variant_id}
            for variant_id in ("V0", "V1")
        ]

    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_score_paired_shadow_case",
        fake_score,
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_build_report",
        lambda progress, *, expected_case_count: {"run_id": progress["run_id"]},
    )
    monkeypatch.setattr(
        runtime_variant_shadow_module,
        "_render_report",
        lambda report: "sealed\n",
    )

    await runtime_variant_shadow_module.run_runtime_variant_shadow(
        tmp_path,
        settings=Settings(project_root=tmp_path),
        selection_path=tmp_path / "selection.json",
    )

    first_outcome_index = next(
        index for index, event in enumerate(events) if event.startswith("outcome:")
    )
    if resume_mode == "partial_seals":
        assert "seal:EP-B:V0" in events[:first_outcome_index]
        assert "seal:EP-B:V1" in events[:first_outcome_index]
        assert len(injected_batch_ids) == 2
        assert len(set(injected_batch_ids)) == 1
    else:
        assert not any(event.startswith("seal:") for event in events)
        assert injected_batch_ids == []
        assert "outcome:EP-A" not in events
    assert "verify:EP-A" in events[:first_outcome_index]
    assert "verify:EP-B" in events[:first_outcome_index]


def test_runtime_shadow_holds_without_sealed_relevance_labels() -> None:
    metrics = {
        "recall_at_20": 0.5,
        "precision_at_20": 0.2,
        "mrr": 1.0,
        "brier": 0.1,
        "final_memory_citation_rate": 1.0,
    }
    common = {
        "metrics": metrics,
        "future_record_count": 0,
        "blind_web_call_count": 0,
        "online_full_scan_count": 0,
        "elapsed_seconds": 1.0,
        "prompt_token_count": 100,
        "pre_retrieval_identity": {"source": "same"},
        "adaptive_trace_count": 1,
        "runtime_trace_count": 1,
        "selected_record_count": 7,
        "rare_mechanism_recovered_count": 1,
        "final_cited_record_count": 2,
        "lane_selected_counts": {"POSITIVE_ANALOG": 1},
    }
    progress = {
        "run_id": "RVSHADOW-test",
        "identity": {
            "split": "CALIBRATION",
            "evaluation_scope": "FORMAL_SPLIT",
            "formal_split_case_count": 1,
        },
        "observations": [
            {
                **common,
                "case_id": "CASE-1",
                "variant_id": "V0",
                "offline_unexposed_recovered_count": 0,
            },
            {
                **common,
                "case_id": "CASE-1",
                "variant_id": "V1",
                "offline_unexposed_recovered_count": 7,
            },
        ],
    }

    report = _build_report(progress, expected_case_count=1)

    assert report["paired_case_count"] == 1
    assert report["v0_offline_unexposed_recovered_count"] == 0
    assert report["v1_offline_unexposed_recovered_count"] == 7
    assert report["offline_unexposed_recovered_delta"] == 7
    assert report["gate_checks"]["offline_unexposed_recovery_increased_vs_v0"] is True
    assert report["gate_checks"]["paired_pre_retrieval_identity_match"] is True
    assert report["gate_checks"]["formal_split_case_closure"] is True
    assert report["gate_checks"]["known_relevant_recall_labeled"] is False
    assert report["runtime_gate"] == "HOLD"
    assert report["full_rebuild_decision"] == "HOLD"
    assert report["variant_runtime"]["V1"]["oauth_live_agent_call_count"] == 0
    assert report["system_metric_notes"]["oauth_calls"] == ("LIVE_CODEX_AGENT_EXECUTIONS")
    assert report["paired_bootstrap"]["paired_case_count"] == 1
    assert report["paired_bootstrap"]["metrics"]["recall_at_20"]["improvement_direction_mean"] == 0.0


def test_runtime_counter_delta_separates_live_and_checkpoint_calls(
    tmp_path: Path,
) -> None:
    llm = _CounterLLM()
    embedding = _CounterEmbedding()
    before = _runtime_counter_snapshot(
        tmp_path,
        llm=llm,
        embedding_provider=embedding,
    )
    trace_dir = tmp_path / "runs" / "traces"
    trace_dir.mkdir(parents=True)
    write_json(
        trace_dir / "trace.json",
        {
            "operation": "generate_structured",
            "status": "checkpoint_hit",
            "started_at": (parse_datetime(str(before["captured_at"])) + timedelta(seconds=2)).isoformat(),
            "token_usage": {
                "prompt_tokens_estimate": 11,
                "completion_tokens_estimate": 5,
            },
        },
    )
    llm.live = 2
    llm.cache = 1
    embedding.embedding_query_count = 3
    embedding.embedding_text_count = 7
    embedding.embedding_input_char_count = 101

    observed = _runtime_counter_delta(
        tmp_path,
        before=before,
        llm=llm,
        embedding_provider=embedding,
    )

    assert observed["logical_llm_call_count"] == 1
    assert observed["oauth_live_agent_call_count"] == 2
    assert observed["llm_checkpoint_hit_count"] == 1
    assert observed["oauth_cache_event_count"] == 1
    assert observed["llm_prompt_tokens_estimate"] == 11
    assert observed["llm_completion_tokens_estimate"] == 5
    assert observed["embedding_query_count"] == 3
    assert observed["embedding_text_count"] == 7
    assert observed["pre_llm_latency_seconds"] == 2.0
    assert observed["pre_llm_latency_status"] == "MEASURED_TO_FIRST_LLM_TRACE"


def test_runtime_trace_paths_prefer_finalized_trace_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_path = tmp_path / "runs" / "initial-trace.json"
    final_path = tmp_path / "runs" / "final-trace.json"
    write_json(initial_path, {"stage": "initial"})
    write_json(final_path, {"stage": "final", "final_candidate_ids": ["candidate:1:000001"]})
    final_manifest_path = tmp_path / "runs" / "runtime_retrieval_final_manifest.json"
    write_json(
        final_manifest_path,
        {
            "schema_version": "nslab.runtime_retrieval_final_manifest.v1",
            "run_id": "RUN-FINAL-TRACE",
            "trace_count": 1,
            "traces": [
                {
                    "cluster_id": "CLUSTER-FINAL-TRACE",
                    "artifact_path": final_path.relative_to(tmp_path).as_posix(),
                    "sha256": file_sha256(final_path),
                }
            ],
        },
    )
    context = SimpleNamespace(
        run_id="RUN-FINAL-TRACE",
        runtime_retrieval_cluster_ids=["CLUSTER-FINAL-TRACE"],
        runtime_retrieval_traces=[
            SimpleNamespace(
                artifact_path=initial_path.relative_to(tmp_path).as_posix()
            )
        ],
    )
    manifest = SimpleNamespace(
        daily_memory_context_summary={
            "runtime_retrieval_final_manifest_artifact": (
                final_manifest_path.relative_to(tmp_path).as_posix()
            ),
            "runtime_retrieval_final_manifest_sha256": file_sha256(
                final_manifest_path
            ),
        }
    )
    verified_paths = {
        final_manifest_path.resolve(): 0,
        final_path.resolve(): 0,
    }
    original_read_bytes = Path.read_bytes

    def coordinated_swap(path: Path) -> bytes:
        resolved = path.resolve()
        if resolved in verified_paths:
            verified_paths[resolved] += 1
            if verified_paths[resolved] > 1:
                return b'{"coordinated_swap":true}\n'
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", coordinated_swap)

    observed = _runtime_trace_paths(
        tmp_path,
        manifest=manifest,
        context=context,
    )

    assert observed == [final_path.resolve()]
    assert verified_paths == {
        final_manifest_path.resolve(): 1,
        final_path.resolve(): 1,
    }
