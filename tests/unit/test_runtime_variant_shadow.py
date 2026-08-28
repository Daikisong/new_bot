from __future__ import annotations

import csv
import json
from datetime import timedelta
from pathlib import Path

import pytest

from news_scalping_lab.evaluation.runtime_variant_shadow import (
    _aggregate_micro_market_metrics,
    _build_report,
    _expected_calibration_error,
    _materialize_case_news,
    _precision_at,
    _recall_at,
    _runtime_counter_delta,
    _runtime_counter_snapshot,
)
from news_scalping_lab.utils import file_sha256, parse_datetime, write_json


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


def test_shadow_news_reconstruction_uses_only_cutoff_safe_rows(tmp_path: Path) -> None:
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

    path, _digest, cutoff = _materialize_case_news(
        tmp_path,
        output_dir=tmp_path / "output",
        case=case,
    )

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert cutoff == "2026-01-02T08:59:59+09:00"
    assert rows == [
        {
            "date": "2026-01-02",
            "time": "08:30:00",
            "title": "cutoff-safe title",
            "body": "cutoff-safe body",
        }
    ]


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
