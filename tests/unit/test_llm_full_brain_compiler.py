from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path
from typing import TypeVar

import pytest
from pydantic import BaseModel

import news_scalping_lab.brain.compiler as compiler_module
from news_scalping_lab.brain.compiler import BRAIN_FILES, BrainCompiler
from news_scalping_lab.llm.mock import DeterministicMockLLMProvider
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.retrieval.embedding import AsyncEmbeddingProviderAdapter
from news_scalping_lab.utils import KST, canonical_json, read_json, sha256_text

T = TypeVar("T", bound=BaseModel)


@pytest.fixture(autouse=True)
def _llm_full_fixture_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        compiler_module,
        "now_kst",
        lambda: datetime(2031, 1, 1, tzinfo=KST),
    )
    monkeypatch.setattr(
        compiler_module,
        "create_production_embedding_provider",
        _test_production_embedding_provider,
    )


class RecordingBrainLLM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.embed_calls: list[tuple[str, list[str]]] = []
        self.embedding_model = "embed-brain-test"

    async def generate_text(self, *, prompt: str, purpose: str) -> str:
        self.calls.append((purpose, prompt))
        return f"{purpose} synthesized output"

    async def generate_structured(
        self,
        *,
        prompt: str,
        response_model: type[T],
        purpose: str,
    ) -> T:
        raise AssertionError("llm-full brain compile should use text synthesis")

    async def embed(self, *, texts: list[str], purpose: str) -> list[list[float]]:
        self.embed_calls.append((purpose, list(texts)))
        return [[float(index + 1), float(len(text) % 7)] for index, text in enumerate(texts)]


def _test_production_embedding_provider(
    settings: object,
    *,
    require_records: bool,
    provider: object | None = None,
) -> AsyncEmbeddingProviderAdapter:
    del require_records
    if provider is None:
        raise AssertionError("llm-full fixture must pass its embedding provider")
    model = getattr(provider, "embedding_model", None)
    if not isinstance(model, str) or not model:
        raise AssertionError("llm-full fixture provider must expose embedding_model")
    configured_provider = getattr(settings, "embedding_provider", "openai")
    return AsyncEmbeddingProviderAdapter(
        provider,
        embedding_method=f"real_embedding:{configured_provider}:{model}",
        production_capability_attested=True,
    )


def test_ineligible_record_is_tentative_boundary_not_positive_support() -> None:
    record = _record(
        "BRAIN-SEMANTIC-EXCLUDED",
        record_type="supervised_direct_event_case",
        training_target="direct_event_response",
        response_class="positive_high10",
        training_eligible=False,
        eligibility_reason="semantic_contract_failed",
        payload_extra={
            "training_exclusion_reason": "semantic_contract_failed",
            "semantic_exclusion_relation_ids": ["CAND-1"],
        },
    )

    compact = compiler_module._compact_record_for_prompt(record)
    claims = compiler_module._compiled_claims_from_records([record])
    shard_prompt = json.loads(
        compiler_module._brain_record_shard_prompt(
            shard_index=1,
            records=[record],
            brain_version="brain-test",
            provider_name="openai",
            model="test-model",
        )
    )

    assert compact["training_eligible"] is False
    assert compact["eligibility_reason"] == "semantic_contract_failed"
    assert compact["training_exclusion_reason"] == "semantic_contract_failed"
    assert compact["semantic_exclusion_relation_ids"] == ["CAND-1"]
    assert claims == []
    assert compact["evidence_polarity"] == "POSITIVE"
    assert compact["label_quality"] == "missing"
    assert compact["routing_disposition"] == "AUDIT"
    assert "A positive claim requires training_eligible=true" in shard_prompt["instruction"]
    assert "audit context only" in shard_prompt["instruction"]


def test_positive_candidate_error_is_correction_only_not_positive_support() -> None:
    record = _record(
        "BRAIN-CANDIDATE-ERROR-POSITIVE",
        record_type="candidate_generation_error_case",
        training_target="candidate_generation_correction",
        response_class="positive_high10",
        training_eligible=True,
        payload_extra={"outcome_high_return_pct": 29.0},
    )

    compact = compiler_module._compact_record_for_prompt(record)
    shard_prompt = json.loads(
        compiler_module._brain_record_shard_prompt(
            shard_index=1,
            records=[record],
            brain_version="brain-test",
            provider_name="openai",
            model="test-model",
        )
    )
    claim = compiler_module._compiled_claims_from_records([record])[0]

    assert compact["evidence_polarity"] == "POSITIVE"
    assert compact["positive_support_eligible"] is False
    assert compact["memory_lanes"] == ["candidate_generation_errors"]
    assert claim.positive_case_count == 0
    assert "Candidate-error records are correction evidence only" in shard_prompt["instruction"]


def test_eligible_negative_control_is_supported_negative_not_positive() -> None:
    record = _record(
        "BRAIN-NEGATIVE-CONTROL",
        record_type="negative_control_case",
        training_target="negative_control_calibration",
        response_class="negative_control",
        training_eligible=True,
        payload_extra={"outcome_high_return_pct": 1.0},
    )

    claim = compiler_module._compiled_claims_from_records([record])[0]

    assert claim.category == "failure_modes"
    assert claim.status == "supported"
    assert claim.positive_case_count == 0
    assert claim.negative_case_count == 1
    assert claim.near_miss_count == 0


def test_large_brain_shard_uses_full_coverage_groups_and_bounded_representatives() -> None:
    records = [
        _record(
            f"BRAIN-GROUPED-{index:05d}",
            record_type="supervised_direct_event_case",
            training_target="direct_event_response",
            response_class="positive_high10",
            payload_extra={
                "event_id": f"EVENT-{index:05d}",
                "sample_weight": float(index % 7) / 10.0,
            },
        )
        for index in range(1_000)
    ]

    prompt = json.loads(
        compiler_module._brain_record_shard_prompt(
            shard_index=1,
            records=records,
            brain_version="brain-grouped",
            provider_name="codex-oauth",
            model="gpt-5.6-sol",
            reasoning_effort="xhigh",
        )
    )

    assert prompt["map_reduce_version"] == compiler_module.LLM_FULL_MAP_REDUCE_VERSION
    assert prompt["model"] == "gpt-5.6-sol"
    assert prompt["reasoning_effort"] == "xhigh"
    assert prompt["source_record_count"] == len(records)
    assert prompt["source_record_ids_sha256"] == sha256_text(
        canonical_json(sorted(record.record_id for record in records))
    )
    assert sum(group["record_count"] for group in prompt["evidence_groups"]) == len(records)
    assert prompt["representative_record_count"] <= (
        compiler_module.LLM_FULL_SHARD_REPRESENTATIVE_LIMIT
    )
    assert prompt["representative_record_count"] < len(records)
    representative_ids = {
        record["record_id"]
        for lane in ("reasoning_records", "audit_context_records")
        for record in prompt[lane]
    }
    assert len(representative_ids) == prompt["representative_record_count"]
    assert all(
        set(group["representative_record_ids"]) <= representative_ids
        for group in prompt["evidence_groups"]
    )


def test_llm_full_production_population_uses_bounded_shard_count() -> None:
    records = [None] * 823_279

    shards = compiler_module._record_shards(
        records,
        compiler_module.LLM_FULL_RECORD_SHARD_SIZE,
    )

    assert compiler_module.LLM_FULL_RECORD_SHARD_SIZE == 20_000
    assert len(shards) == 42
    assert sum(len(shard) for shard in shards) == len(records)


def test_compact_shard_summaries_preserve_hashes_not_full_id_lists() -> None:
    record_ids = [f"RECORD-{index:05d}" for index in range(100)]
    summary = "x" * (compiler_module.LLM_FULL_SHARD_SUMMARY_MAX_CHARS + 100)
    compact = compiler_module._compact_shard_summaries(
        [
            {
                "shard_index": 1,
                "record_ids": record_ids,
                "record_ids_sha256": sha256_text(canonical_json(record_ids)),
                "record_count": len(record_ids),
                "summary": summary,
            }
        ]
    )[0]

    assert "record_ids" not in compact
    assert compact["record_ids_sha256"] == sha256_text(canonical_json(record_ids))
    assert compact["representative_record_ids"] == record_ids[
        : compiler_module.LLM_FULL_SHARD_SUMMARY_RECORD_ID_LIMIT
    ]
    assert compact["summary_sha256"] == sha256_text(summary)
    assert compact["summary_truncated"] is True
    assert compact["summary"].endswith("...[truncated]")


def test_llm_brain_cache_identity_includes_reasoning_effort(tmp_path: Path) -> None:
    provider = RecordingBrainLLM()
    common = {
        "provider": provider,
        "cache_dir": tmp_path,
        "purpose": "brain_compile:reasoning-cache-test",
        "prompt": "same prompt",
        "record_ids": [],
        "record_hashes": {},
        "provider_name": "codex-oauth",
        "model": "gpt-5.6-sol",
    }

    high = asyncio.run(
        compiler_module._cached_generate_text(
            **common,
            reasoning_effort="high",
        )
    )
    xhigh = asyncio.run(
        compiler_module._cached_generate_text(
            **common,
            reasoning_effort="xhigh",
        )
    )

    assert high[1] != xhigh[1]
    assert len(provider.calls) == 2


def test_llm_full_brain_compile_uses_map_reduce_review_and_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_openai_config(tmp_path)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    llm = RecordingBrainLLM()
    monkeypatch.setattr(compiler_module, "create_llm_provider", lambda settings: llm)
    _write_records(
        tmp_path,
        [
            _record(
                "BRAIN-DIRECT",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
                payload_extra={
                    "issuer_day_case_id": "20300110:000001",
                    "ticker": "000001",
                    "event_id": "EVT-1",
                    "path_type": "SINGLE_EVENT",
                    "event_ids": ["EVT-1", "EVT-2"],
                    "safe_D1_features": {"gap_rate": 0.03, "volume_rank": 2},
                    "D_outcome": {"label_quality": "verified", "return_pct": 12.4},
                    "sample_weight": 0.75,
                    "attribution_status": "direct_event_supported",
                },
            ),
            _record(
                "BRAIN-COUNTER",
                record_type="counterexample",
                training_target="counterexample",
                response_class="negative_control",
                payload_extra={
                    "counterexample_id": "CE-1",
                    "path_type": "THEME_BENEFICIARY",
                    "outcome": {"label_quality": "verified", "return_pct": -3.0},
                },
            ),
        ],
    )

    manifest = BrainCompiler(tmp_path).rebuild(mode="llm-full")

    purposes = [purpose for purpose, _prompt in llm.calls]
    assert manifest.build_mode == "llm-full"
    assert manifest.catalog_only is False
    assert "brain_compile:shard:0001" in purposes
    assert len([purpose for purpose in purposes if ":synthesis:" in purpose]) == len(BRAIN_FILES)
    assert len([purpose for purpose in purposes if ":review:" in purpose]) == len(BRAIN_FILES)
    shard_prompt = json.loads(next(prompt for purpose, prompt in llm.calls if purpose == "brain_compile:shard:0001"))
    shard_direct_record = next(
        record for record in shard_prompt["reasoning_records"] if record["record_id"] == "BRAIN-DIRECT"
    )
    assert shard_direct_record["routing_features"] == {
        "record_type": "supervised_direct_event_case",
        "training_target": "direct_event_response",
        "evidence_phase": "POSTMORTEM",
        "path_type": "SINGLE_EVENT",
        "response_class": "positive_high10",
        "attribution_status": "direct_event_supported",
    }
    assert shard_direct_record["payload_summary"]["issuer_day_case_id"] == ("20300110:000001")
    assert shard_direct_record["payload_summary"]["safe_D1_features"] == {
        "gap_rate": 0.03,
        "volume_rank": 2,
    }
    assert shard_direct_record["payload_summary"]["D_outcome"] == {
        "label_quality": "verified",
        "return_pct": 12.4,
    }
    single_event_prompt = json.loads(
        next(prompt for purpose, prompt in llm.calls if purpose == "brain_compile:synthesis:single_event")
    )
    assert single_event_prompt["category_guidance"]["focus"] == (
        "direct event response patterns and issuer-day evidence"
    )
    assert single_event_prompt["category_guidance"]["primary_record_types"] == [
        "supervised_direct_event_case",
        "supervised_issuer_day_case",
    ]
    assert single_event_prompt["category_guidance"]["must_cite_record_ids"] is True
    single_event_record = single_event_prompt["reasoning_records"][0]
    assert single_event_record["payload_summary"]["event_ids"] == ["EVT-1", "EVT-2"]
    assert single_event_record["payload_summary"]["sample_weight"] == 0.75
    counterexample_review_prompt = json.loads(
        next(prompt for purpose, prompt in llm.calls if purpose == "brain_compile:review:counterexamples")
    )
    assert counterexample_review_prompt["category_guidance"]["review_targets"] == [
        "positive claims without contradiction checks",
        "negative evidence hidden in generic caveats",
    ]
    compile_manifest = read_json(tmp_path / "brain" / "current" / "llm_compile_manifest.json")
    compile_report = read_json(tmp_path / "diagnostics" / "brain_compile_report.json")
    brain_manifest = read_json(tmp_path / "brain" / "current" / "brain_manifest.json")
    vector_manifest = read_json(tmp_path / "memory" / "vector_index" / "manifest.json")
    production_pointer = read_json(tmp_path / "memory" / "retrieval_index" / "current.json")
    production_manifest = read_json(tmp_path / production_pointer["manifest_path"])
    compiled_claims = [
        json.loads(line)
        for line in (tmp_path / "brain" / "current" / "compiled_claims.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    compiled_claims_by_record = {claim["supporting_record_ids"][0]: claim for claim in compiled_claims}
    assert compile_manifest["compiler_version"] == compiler_module.LLM_FULL_COMPILER_VERSION
    assert compile_manifest["map_reduce_version"] == compiler_module.LLM_FULL_MAP_REDUCE_VERSION
    assert compile_manifest["schema_version"] == (
        "nslab.llm_full_brain_compile_manifest.v2"
    )
    assert compile_manifest["reasoning_effort"] == "low"
    assert compile_report["llm_compile_run"]["reasoning_effort"] == "low"
    assert brain_manifest["catalog_only"] is False
    assert brain_manifest["catalog_mode_reason"] is None
    assert brain_manifest["deprecated_mode_alias"] is False
    assert brain_manifest["production_eligible"] is True
    assert compile_manifest["record_shard_count"] == 1
    assert compile_manifest["category_count"] == len(BRAIN_FILES)
    assert compile_manifest["compiled_claim_count"] == 1
    assert compile_manifest["llm_generation_count"] == 1 + len(BRAIN_FILES) * 2
    assert compile_manifest["record_shards"][0]["prompt_sha256"]
    assert all(
        category["synthesis_prompt_sha256"] and category["review_prompt_sha256"]
        for category in compile_manifest["categories"]
    )
    assert "llm_live_call_count" not in compile_manifest
    assert "cache_hit" not in compile_manifest["record_shards"][0]
    assert "synthesis_cache_hit" not in compile_manifest["categories"][0]
    assert compile_report["schema_version"] == "nslab.brain_compile_diagnostics.v1"
    assert compile_report["compiler_mode"] == "llm-full"
    assert compile_report["catalog_only"] is False
    assert compile_report["catalog_mode_reason"] is None
    assert compile_report["deprecated_mode_alias"] is False
    assert compile_report["production_eligible"] is True
    assert compile_report["compiler_provider"] == "openai"
    assert compile_report["compiler_model"] == "test-brain-model"
    assert compile_report["compiler_version"] == compiler_module.LLM_FULL_COMPILER_VERSION
    assert compile_report["compiled_claim_count"] == 1
    counterexample_category = next(
        category for category in compile_manifest["categories"] if category["category"] == "counterexamples"
    )
    assert counterexample_category["reasoning_support_record_ids"] == []
    assert counterexample_category["non_reasoning_record_ids"] == ["BRAIN-COUNTER"]
    counterexample_text = (tmp_path / "brain" / "current" / "07_counterexamples.md").read_text(encoding="utf-8")
    assert "## Context And Excluded Records" in counterexample_text
    assert "`BRAIN-COUNTER` (counterexample; AUDIT)" in counterexample_text
    assert compile_report["llm_compile_present"] is True
    assert compile_report["llm_compile_run_present"] is True
    assert compile_report["llm_compile_run"]["llm_generation_count"] == (1 + len(BRAIN_FILES) * 2)
    assert (
        compile_report["llm_compile_run"]["llm_live_call_count"]
        == compile_report["llm_compile_run"]["llm_generation_count"]
    )
    assert compile_report["llm_compile_run"]["llm_cache_hit_count"] == 0
    assert compile_report["llm_compile_run"]["all_outputs_from_cache"] is False
    assert compile_report["llm_compile_run"]["record_shards"][0]["cache_hit"] is False
    assert (
        compile_report["llm_compile_run"]["record_shards"][0]["prompt_sha256"]
        == compile_manifest["record_shards"][0]["prompt_sha256"]
    )
    assert all(
        category["synthesis_cache_hit"] is False and category["review_cache_hit"] is False
        for category in compile_report["llm_compile_run"]["categories"]
    )
    assert [
        (
            category["synthesis_prompt_sha256"],
            category["review_prompt_sha256"],
        )
        for category in compile_report["llm_compile_run"]["categories"]
    ] == [
        (
            category["synthesis_prompt_sha256"],
            category["review_prompt_sha256"],
        )
        for category in compile_manifest["categories"]
    ]
    trace_payloads = [read_json(path) for path in sorted((tmp_path / "runs" / "traces").glob("*.json"))]
    compile_traces = [
        trace
        for trace in trace_payloads
        if trace.get("operation") == "generate_text"
        and isinstance(trace.get("purpose"), str)
        and trace["purpose"].startswith("brain_compile:")
    ]
    assert len(compile_traces) == compile_manifest["llm_generation_count"]
    assert {trace["input"]["prompt_sha256"] for trace in compile_traces} == _compile_run_prompt_hashes(
        compile_report["llm_compile_run"]
    )
    assert all(trace["status"] == "ok" for trace in compile_traces)
    assert all(
        trace["model_config"]["configured_provider"] == "openai"
        and trace["model_config"]["provider_class"] == "RecordingBrainLLM"
        and trace["model_config"]["model"] == "test-brain-model"
        and trace["model_config"]["embedding_model"] == "embed-brain-test"
        and trace["model_config"]["compiler_version"] == compiler_module.LLM_FULL_COMPILER_VERSION
        for trace in compile_traces
    )
    for trace in compile_traces:
        checkpoint_path = tmp_path / "runs" / "checkpoints" / "llm" / f"{trace['checkpoint_id']}.json"
        checkpoint = read_json(checkpoint_path)
        assert checkpoint["schema_version"] == "nslab.llm_checkpoint.v1"
        assert checkpoint["operation"] == trace["operation"]
        assert checkpoint["purpose"] == trace["purpose"]
        assert checkpoint["input"] == trace["input"]
        assert checkpoint["model_config"] == trace["model_config"]
    assert compile_report["category_claim_counts"]["single_event"] == 1
    assert compile_report["category_claim_counts"]["counterexamples"] == 0
    assert compile_report["category_source_record_counts"]["single_event"] == 1
    assert compile_report["record_coverage"]["accepted_record_count"] == 2
    assert compile_report["record_coverage"]["swept_record_count"] == 2
    assert compile_report["record_coverage"]["coverage_complete"] is True
    assert len(compiled_claims) == 1
    assert compiled_claims_by_record["BRAIN-DIRECT"]["category"] == "single_event"
    assert compiled_claims_by_record["BRAIN-DIRECT"]["status"] == "supported"
    assert compiled_claims_by_record["BRAIN-DIRECT"]["positive_case_count"] == 1
    assert "BRAIN-COUNTER" not in compiled_claims_by_record
    single_event_category = next(
        category for category in compile_manifest["categories"] if category["category"] == "single_event"
    )
    assert single_event_category["compiled_claim_ids"] == [compiled_claims_by_record["BRAIN-DIRECT"]["claim_id"]]
    assert vector_manifest["embedding_method"] == "deterministic_hashing_v1"
    assert production_manifest["embedding_model"] == "real_embedding:openai:embed-brain-test"
    assert production_manifest["production_ready"] is True
    assert production_manifest["hnsw_index_ready"] is True
    assert production_manifest["fts_index_ready"] is True
    assert vector_manifest["dimensions"] == 32
    assert production_manifest["embedding_dimensions"] == 2
    assert llm.embed_calls
    single_event = (tmp_path / "brain" / "current" / "01_single_event_patterns.md").read_text(encoding="utf-8")
    assert "## Category Synthesis" in single_event
    assert "## Contradiction And Boundary Review" in single_event
    assert len(list((tmp_path / "brain" / "llm_cache").glob("*.json"))) == (1 + len(BRAIN_FILES) * 2)

    llm.calls.clear()
    llm.embed_calls.clear()
    second_manifest = BrainCompiler(tmp_path).rebuild(mode="llm-full")
    second_compile_manifest = read_json(tmp_path / "brain" / "current" / "llm_compile_manifest.json")
    second_compile_report = read_json(tmp_path / "diagnostics" / "brain_compile_report.json")

    assert second_manifest.brain_version == manifest.brain_version
    assert llm.calls == []
    assert llm.embed_calls == []
    assert second_compile_manifest["llm_generation_count"] == 1 + len(BRAIN_FILES) * 2
    assert second_compile_manifest == compile_manifest
    assert second_compile_report["llm_compile_run"]["llm_live_call_count"] == 0
    assert (
        second_compile_report["llm_compile_run"]["llm_cache_hit_count"]
        == (second_compile_report["llm_compile_run"]["llm_generation_count"])
    )
    assert second_compile_report["llm_compile_run"]["all_outputs_from_cache"] is True
    assert all(shard["cache_hit"] is True for shard in second_compile_report["llm_compile_run"]["record_shards"])
    assert all(
        category["synthesis_cache_hit"] is True and category["review_cache_hit"] is True
        for category in second_compile_report["llm_compile_run"]["categories"]
    )


def test_llm_full_category_routing_uses_continuation_records() -> None:
    records = [
        _record(
            "BRAIN-DIRECT",
            record_type="supervised_direct_event_case",
            training_target="direct_event_response",
            response_class="positive_high10",
        ),
        _record(
            "BRAIN-EDGE",
            record_type="event_ticker_edge",
            training_target="event_ticker_relation",
            response_class="continuation_edge",
        ),
        _record(
            "BRAIN-MEMORY",
            record_type="company_memory_delta",
            training_target="company_memory",
            response_class="asof_memory",
        ),
    ]

    continuation_ids = [record.record_id for record in compiler_module._records_for_category(records, "continuation")]

    assert continuation_ids == ["BRAIN-EDGE", "BRAIN-MEMORY"]


def test_llm_full_category_routing_does_not_fallback_to_unrelated_records() -> None:
    records = [
        _record(
            "BRAIN-DIRECT",
            record_type="supervised_direct_event_case",
            training_target="direct_event_response",
            response_class="positive_high10",
        ),
        _record(
            "BRAIN-COUNTER",
            record_type="counterexample",
            training_target="counterexample",
            response_class="negative_control",
        ),
    ]

    theme_records = compiler_module._records_for_category(records, "theme_formation")
    world_model_records = compiler_module._records_for_category(records, "world_model")

    assert theme_records == []
    assert [record.record_id for record in world_model_records] == [
        "BRAIN-DIRECT",
        "BRAIN-COUNTER",
    ]


def test_llm_full_category_routing_includes_repaired_gold_record_types() -> None:
    records = [
        _record(
            "BRAIN-THEME",
            record_type="theme_formation_case",
            training_target="theme_formation_response",
            response_class="NO_RESPONSE",
        ),
        _record(
            "BRAIN-NEGATIVE",
            record_type="negative_control_case",
            training_target="negative_control_calibration",
            response_class="negative_control",
        ),
        _record(
            "BRAIN-NEWSLESS",
            record_type="newsless_or_unexplained_case",
            training_target="newsless_outcome_calibration",
            response_class="NEWSLESS_OR_UNEXPLAINED",
        ),
        _record(
            "BRAIN-CONTEXT",
            record_type="context_market_state_or_fact_case",
            training_target="context_memory",
            response_class="context",
        ),
    ]

    assert [record.record_id for record in compiler_module._records_for_category(records, "theme_formation")] == [
        "BRAIN-THEME"
    ]
    assert [record.record_id for record in compiler_module._records_for_category(records, "failure_modes")] == [
        "BRAIN-NEGATIVE",
        "BRAIN-NEWSLESS",
    ]
    assert [record.record_id for record in compiler_module._records_for_category(records, "market_memory")] == [
        "BRAIN-CONTEXT"
    ]


def test_catalog_brain_marked_catalog_only(tmp_path: Path) -> None:
    manifest = BrainCompiler(tmp_path).rebuild(mode="catalog")
    brain_manifest = read_json(tmp_path / "brain" / "current" / "brain_manifest.json")
    compile_report = read_json(tmp_path / "diagnostics" / "brain_compile_report.json")

    assert manifest.build_mode == "catalog"
    assert manifest.catalog_only is True
    assert brain_manifest["build_mode"] == "catalog"
    assert brain_manifest["catalog_only"] is True
    assert brain_manifest["catalog_mode_reason"] == "explicit_catalog_mode"
    assert brain_manifest["deprecated_mode_alias"] is False
    assert brain_manifest["production_eligible"] is False
    assert compile_report["compiler_mode"] == "catalog"
    assert compile_report["catalog_only"] is True
    assert compile_report["catalog_mode_reason"] == "explicit_catalog_mode"
    assert compile_report["deprecated_mode_alias"] is False
    assert compile_report["production_eligible"] is False


def test_brain_category_files_are_not_identical(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _rebuild_llm_full_fixture(
        tmp_path,
        monkeypatch,
        [
            _record(
                "BRAIN-DIRECT",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
                payload_extra={"path_type": "SINGLE_EVENT"},
            ),
            _record(
                "BRAIN-COUNTER",
                record_type="counterexample",
                training_target="counterexample",
                response_class="negative_control",
            ),
        ],
    )

    category_texts = [
        (tmp_path / "brain" / "current" / file_name).read_text(encoding="utf-8") for file_name in BRAIN_FILES
    ]

    assert len(set(category_texts)) == len(BRAIN_FILES)


def test_compiled_claims_reference_existing_records(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records = [
        _record(
            "BRAIN-DIRECT",
            record_type="supervised_direct_event_case",
            training_target="direct_event_response",
            response_class="positive_high10",
            payload_extra={"path_type": "SINGLE_EVENT"},
        ),
        _record(
            "BRAIN-COUNTER",
            record_type="counterexample",
            training_target="counterexample",
            response_class="negative_control",
        ),
    ]
    _rebuild_llm_full_fixture(tmp_path, monkeypatch, records)
    record_ids = {record.record_id for record in records}
    compiled_claims = [
        json.loads(line)
        for line in (tmp_path / "brain" / "current" / "compiled_claims.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert compiled_claims
    for claim in compiled_claims:
        supporting_ids = set(claim["supporting_record_ids"])
        contradicting_ids = set(claim["contradicting_record_ids"])
        assert supporting_ids
        assert supporting_ids <= record_ids
        assert contradicting_ids <= record_ids


def test_single_episode_cannot_validate_rule(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _rebuild_llm_full_fixture(
        tmp_path,
        monkeypatch,
        [
            _record(
                "BRAIN-SOLO-DIRECT",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
                payload_extra={"path_type": "SINGLE_EVENT"},
            ),
        ],
    )

    compiled_claims = [
        json.loads(line)
        for line in (tmp_path / "brain" / "current" / "compiled_claims.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert len(compiled_claims) == 1
    claim = compiled_claims[0]
    assert claim["supporting_record_ids"] == ["BRAIN-SOLO-DIRECT"]
    assert claim["supporting_episode_ids"] == ["EP-llm-full"]
    assert claim["positive_case_count"] == 1
    assert claim["status"] != "validated"
    assert "do not promote one record to a validated rule without broader evidence" in (claim["boundary_conditions"])


def test_full_rebuild_from_raw_is_reproducible_with_cache(
    tmp_path: Path,
    monkeypatch,
) -> None:
    llm, first_manifest = _rebuild_llm_full_fixture(
        tmp_path,
        monkeypatch,
        [
            _record(
                "BRAIN-REPRO-DIRECT",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
                payload_extra={"path_type": "SINGLE_EVENT"},
            ),
            _record(
                "BRAIN-REPRO-COUNTER",
                record_type="counterexample",
                training_target="counterexample",
                response_class="negative_control",
            ),
        ],
    )
    first_compile_manifest = read_json(tmp_path / "brain" / "current" / "llm_compile_manifest.json")
    first_claims = (tmp_path / "brain" / "current" / "compiled_claims.jsonl").read_text(encoding="utf-8")
    cache_files = sorted(path.name for path in (tmp_path / "brain" / "llm_cache").glob("*.json"))

    llm.calls.clear()
    llm.embed_calls.clear()
    second_manifest = BrainCompiler(tmp_path).rebuild(mode="llm-full")
    second_compile_manifest = read_json(tmp_path / "brain" / "current" / "llm_compile_manifest.json")
    second_compile_report = read_json(tmp_path / "diagnostics" / "brain_compile_report.json")
    second_claims = (tmp_path / "brain" / "current" / "compiled_claims.jsonl").read_text(encoding="utf-8")

    assert second_manifest.brain_version == first_manifest.brain_version
    assert second_compile_manifest == first_compile_manifest
    assert second_claims == first_claims
    assert sorted(path.name for path in (tmp_path / "brain" / "llm_cache").glob("*.json")) == (cache_files)
    assert llm.calls == []
    assert llm.embed_calls == []
    assert second_compile_report["llm_compile_run"]["llm_live_call_count"] == 0
    assert second_compile_report["llm_compile_run"]["all_outputs_from_cache"] is True


def test_llm_full_embedding_model_change_creates_new_brain_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm, first = _rebuild_llm_full_fixture(
        tmp_path,
        monkeypatch,
        [
            _record(
                "BRAIN-EMBEDDING-VERSION",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
                payload_extra={"title": "issuer supply contract signed"},
            )
        ],
    )
    llm.embedding_model = "embed-brain-test-v2"

    second = BrainCompiler(tmp_path).rebuild(mode="llm-full")

    assert second.brain_version != first.brain_version
    assert (
        second.production_memory_snapshot_id
        != first.production_memory_snapshot_id
    )


def test_llm_full_requires_real_provider(
    tmp_path: Path,
    monkeypatch,
) -> None:
    mock_provider_root = tmp_path / "mock-provider"
    _write_llm_config(mock_provider_root, llm_provider="mock")
    _write_records(
        mock_provider_root,
        [
            _record(
                "BRAIN-MOCK-PROVIDER",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
            ),
        ],
    )
    monkeypatch.delenv("NSLAB_LLM_PROVIDER", raising=False)

    with pytest.raises(ValueError, match="requires a real LLM provider"):
        BrainCompiler(mock_provider_root).rebuild(mode="llm-full")

    mock_profile_root = tmp_path / "mock-profile"
    _write_llm_config(
        mock_profile_root,
        llm_provider="openai",
        model_provider="mock",
    )
    _write_records(
        mock_profile_root,
        [
            _record(
                "BRAIN-MOCK-PROFILE",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
            ),
        ],
    )

    with pytest.raises(ValueError, match="requires a non-mock model profile"):
        BrainCompiler(mock_profile_root).rebuild(mode="llm-full")

    mock_factory_root = tmp_path / "mock-factory"
    _write_openai_config(mock_factory_root)
    _write_records(
        mock_factory_root,
        [
            _record(
                "BRAIN-MOCK-FACTORY",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
            ),
        ],
    )
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        compiler_module,
        "create_llm_provider",
        lambda settings: DeterministicMockLLMProvider(model="deterministic-mock"),
    )

    with pytest.raises(ValueError, match="cannot use the mock LLM provider"):
        BrainCompiler(mock_factory_root).rebuild(mode="llm-full")

    for root in (mock_provider_root, mock_profile_root, mock_factory_root):
        assert not (root / "brain" / "current" / "brain_manifest.json").exists()
        assert not (root / "brain" / "current" / "llm_compile_manifest.json").exists()


@pytest.mark.parametrize(
    ("configured_line", "replacement", "message"),
    [
        (
            "evidence_policy: csv-memory-only-strict",
            "evidence_policy: postclose-web-audit-optional",
            "requires CSV_MEMORY_ONLY_STRICT",
        ),
        (
            "web_provider: disabled",
            "web_provider: mock",
            "requires disabled web",
        ),
        (
            "event_cluster_fallback_policy: fail-closed",
            "event_cluster_fallback_policy: allow-deterministic-fallback",
            "requires fail-closed embeddings",
        ),
    ],
)
def test_llm_full_requires_strict_production_policies(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_line: str,
    replacement: str,
    message: str,
) -> None:
    _write_openai_config(tmp_path)
    config_path = tmp_path / "configs" / "default.yaml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            configured_line,
            replacement,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    _write_records(
        tmp_path,
        [
            _record(
                "BRAIN-POLICY",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
            )
        ],
    )

    with pytest.raises(ValueError, match=message):
        BrainCompiler(tmp_path).rebuild(mode="llm-full")

    assert not (tmp_path / "brain" / "current" / "brain_manifest.json").exists()


def test_llm_full_brain_compile_rejects_mock_provider_object(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _write_openai_config(tmp_path)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    monkeypatch.setattr(
        compiler_module,
        "create_llm_provider",
        lambda settings: DeterministicMockLLMProvider(model="deterministic-mock"),
    )
    _write_records(
        tmp_path,
        [
            _record(
                "BRAIN-DIRECT",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
            ),
        ],
    )

    with pytest.raises(ValueError, match="cannot use the mock LLM provider"):
        BrainCompiler(tmp_path).rebuild(mode="llm-full")

    assert not (tmp_path / "brain" / "current" / "brain_manifest.json").exists()
    assert not (tmp_path / "brain" / "current" / "llm_compile_manifest.json").exists()


def _compile_run_prompt_hashes(compile_run: dict[str, object]) -> set[str]:
    prompt_hashes: set[str] = set()
    for shard in compile_run["record_shards"]:
        assert isinstance(shard, dict)
        prompt_hashes.add(str(shard["prompt_sha256"]))
    for category in compile_run["categories"]:
        assert isinstance(category, dict)
        prompt_hashes.add(str(category["synthesis_prompt_sha256"]))
        prompt_hashes.add(str(category["review_prompt_sha256"]))
    return prompt_hashes


def _write_openai_config(root: Path) -> None:
    _write_llm_config(root, llm_provider="openai")


def test_llm_full_publish_rolls_back_every_mutable_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_record = _record(
        "BRAIN-TRANSACTION-1",
        record_type="supervised_direct_event_case",
        training_target="direct_event_response",
        response_class="positive_high10",
        payload_extra={
            "title": "issuer supply contract signed",
            "outcome_high_return_pct": 12.0,
        },
    )
    _rebuild_llm_full_fixture(tmp_path, monkeypatch, [first_record])
    before = _llm_full_mutable_state(tmp_path)
    second_record = _record(
        "BRAIN-TRANSACTION-2",
        record_type="negative_control_case",
        training_target="negative_control_calibration",
        response_class="negative_control",
        payload_extra={
            "title": "issuer supply contract failed",
            "outcome_high_return_pct": -2.0,
        },
    )
    _write_records(tmp_path, [first_record, second_record])

    def fail_activation(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected memory activation failure")

    monkeypatch.setattr(
        compiler_module.ProductionMemoryIndex,
        "activate",
        fail_activation,
    )

    with pytest.raises(RuntimeError, match="injected memory activation failure"):
        BrainCompiler(tmp_path).rebuild(mode="llm-full")

    assert _llm_full_mutable_state(tmp_path) == before


def test_llm_full_retry_reuses_orphan_category_index_after_compile_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_openai_config(tmp_path)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    llm = RecordingBrainLLM()
    monkeypatch.setattr(compiler_module, "create_llm_provider", lambda settings: llm)
    _write_records(
        tmp_path,
        [
            _record(
                "BRAIN-CATEGORY-RETRY",
                record_type="supervised_direct_event_case",
                training_target="direct_event_response",
                response_class="positive_high10",
                payload_extra={
                    "title": "issuer supply contract signed",
                    "outcome_high_return_pct": 12.0,
                },
            )
        ],
    )
    original_compile = compiler_module._compile_llm_category_outputs

    async def fail_after_category_index(*args: object, **kwargs: object) -> object:
        raise RuntimeError("injected category compile failure")

    monkeypatch.setattr(
        compiler_module,
        "_compile_llm_category_outputs",
        fail_after_category_index,
    )
    with pytest.raises(RuntimeError, match="injected category compile failure"):
        BrainCompiler(tmp_path).rebuild(mode="llm-full")
    orphan_manifests = list(
        (
            tmp_path / "runs" / "checkpoints" / "category_brain_index"
        ).glob("*/category_brain_index_manifest.json")
    )
    assert len(orphan_manifests) == 1
    assert not (tmp_path / "brain" / "current" / "brain_manifest.json").exists()

    monkeypatch.setattr(
        compiler_module,
        "_compile_llm_category_outputs",
        original_compile,
    )
    manifest = BrainCompiler(tmp_path).rebuild(mode="llm-full")

    assert manifest.category_brain_index_manifest_artifact == (
        orphan_manifests[0].relative_to(tmp_path).as_posix()
    )
    assert (tmp_path / "brain" / "current" / "brain_manifest.json").exists()


def _write_llm_config(
    root: Path,
    *,
    llm_provider: str,
    model_provider: str | None = None,
) -> None:
    configs = root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "default.yaml").write_text(
        "\n".join(
            [
                f"llm_provider: {llm_provider}",
                "evidence_policy: csv-memory-only-strict",
                "embedding_provider: openai",
                "event_cluster_fallback_policy: fail-closed",
                "web_provider: disabled",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (configs / "models.yaml").write_text(
        "\n".join(
            [
                "openai:",
                f"  provider: {model_provider or 'openai'}",
                "  model: test-brain-model",
                "  embedding_model: embed-brain-test",
                "  max_retries: 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def _rebuild_llm_full_fixture(
    root: Path,
    monkeypatch,
    records: list[BrainRecordEnvelope],
) -> tuple[RecordingBrainLLM, object]:
    _write_openai_config(root)
    monkeypatch.setenv("NSLAB_LLM_PROVIDER", "openai")
    llm = RecordingBrainLLM()
    monkeypatch.setattr(compiler_module, "create_llm_provider", lambda settings: llm)
    _write_records(root, records)
    manifest = BrainCompiler(root).rebuild(mode="llm-full")
    return llm, manifest


def _write_records(root: Path, records: list[BrainRecordEnvelope]) -> None:
    records_dir = root / "memory" / "records"
    records_dir.mkdir(parents=True, exist_ok=True)
    (records_dir / "EP-llm-full.jsonl").write_text(
        "".join(record.model_dump_json() + "\n" for record in records),
        encoding="utf-8",
    )


def _llm_full_mutable_state(root: Path) -> dict[str, bytes]:
    paths = (
        root / "brain" / "current",
        root / "brain" / "claims" / "claims.jsonl",
        root / "brain" / "memory" / "current",
        root / "brain" / "shards" / "current",
        root / "brain" / "HEAD",
        root / "brain" / "diffs",
        root / "diagnostics" / "brain_compile_report.json",
        root / "diagnostics" / "brain_compile_report.md",
        root / "diagnostics" / "record_coverage_report.json",
        root / "diagnostics" / "record_coverage_report.md",
        root / "memory" / "retrieval_index" / "current.json",
        root / "memory" / "retrieval_index" / "as_of_registry.json",
        root / "memory" / "vector_index",
        root / "warehouse",
    )
    state: dict[str, bytes] = {}
    for path in paths:
        if path.is_file():
            state[path.relative_to(root).as_posix()] = path.read_bytes()
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    state[child.relative_to(root).as_posix()] = child.read_bytes()
    return state


def _record(
    record_id: str,
    *,
    record_type: str,
    training_target: str,
    response_class: str,
    payload_extra: dict[str, object] | None = None,
    training_eligible: bool | None = None,
    eligibility_reason: str = "unit test llm-full record",
) -> BrainRecordEnvelope:
    available_from = datetime(2030, 1, 10, 8, 0, 0, tzinfo=KST)
    eligible = record_type != "counterexample" if training_eligible is None else training_eligible
    payload = {
        "record_id": record_id,
        "record_type": record_type,
        "episode_id": "EP-llm-full",
        "trade_date": "2030-01-10",
        "available_from": available_from.isoformat(),
        "training_target": training_target,
        "response_class": response_class,
        "training_eligible": eligible,
    }
    if payload_extra:
        payload.update(payload_extra)
    if record_type in {
        "supervised_direct_event_case",
        "supervised_issuer_day_case",
        "supervised_theme_formation_case",
        "theme_formation_case",
        "beneficiary_discovery_case",
        "candidate_generation_error_case",
        "candidate_ranking_error_case",
        "ranking_error_case",
        "row_disposition_error_case",
        "entity_resolution_error_case",
    } and not any(
        key in payload
        for key in (
            "outcome_high_return_pct",
            "high_return_pct",
            "intraday_high_return_pct",
            "D_high_return_pct",
        )
    ):
        normalized_response = response_class.upper()
        payload["outcome_high_return_pct"] = (
            -1.0
            if any(marker in normalized_response for marker in ("NEGATIVE", "NO_RESPONSE"))
            else 3.0
            if "NEAR_MISS" in normalized_response
            else 12.0
        )
    if record_type == "negative_control_case":
        payload.setdefault("outcome_high_return_pct", 1.0)
    if record_type == "newsless_or_unexplained_case":
        payload.setdefault("outcome_high_return_pct", 18.0)
        payload.setdefault("no_catalyst_asserted", True)
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type=record_type,
        episode_id="EP-llm-full",
        trade_date=date(2030, 1, 10),
        available_from=available_from,
        training_target=training_target,
        evidence_phase="POSTMORTEM",
        training_eligible=eligible,
        eligibility_reason=eligibility_reason,
        status="supported",
        confidence_label="medium",
        provenance_source_ids=["SRC-llm-full"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )
