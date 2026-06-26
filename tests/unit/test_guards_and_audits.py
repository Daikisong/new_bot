from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest

from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.cli import (
    _final_synthesis_manifest_count_mismatches,
    _llm_trace_payload_errors,
)
from news_scalping_lab.context.final_synthesis import (
    FINAL_SYNTHESIS_REQUIRED_INPUTS,
    final_synthesis_input_summary,
)
from news_scalping_lab.contracts.models import BlindAnalysis, OutcomeLabels, ResearchEpisode
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.prices.base import (
    BlindPriceAccessError,
    BlindPriceGuard,
    PriceRecord,
)
from news_scalping_lab.records.models import (
    BrainRecordEnvelope,
    NormalizedEpisodeIndex,
    ResearchBundleEnvelope,
)
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.reporting.sections import PREOPEN_REPORT_SECTION_HEADINGS
from news_scalping_lab.research_import.semantic import (
    SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS,
    build_semantic_import_prompt,
)
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    read_json,
    sha256_text,
    write_json,
)
from news_scalping_lab.web.provider import TemporalWebGuard, WebSearchResult


def _provenance(source_type: str = "test") -> list[dict[str, str]]:
    return [
        {
            "source_id": f"SRC-{source_type}",
            "source_type": source_type,
            "uri": f"test://{source_type}",
            "content_sha256": "a" * 64,
        }
    ]


def _sweep_shard_hash(source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"episode_id": episode_id, "source_sha256": source_hash}
                for episode_id, source_hash in sorted(source_hashes.items())
            ]
        )
    )


def _record_sweep_shard_hash(source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"record_id": record_id, "source_sha256": source_hash}
                for record_id, source_hash in sorted(source_hashes.items())
            ]
        )
    )


def _brain_record_for_sweep_audit(
    record_id: str,
    *,
    episode_id: str,
    available_from: datetime,
) -> BrainRecordEnvelope:
    payload = {
        "record_id": record_id,
        "record_type": "supervised_direct_event_case",
        "episode_id": episode_id,
        "trade_date": "2030-01-09",
        "available_from": available_from.isoformat(),
        "training_target": "direct_event_response",
        "evidence_phase": "BLIND_SAFE",
        "ticker": "000001",
        "company_name": "Record Sweep Audit Co",
        "response_class": "positive_high10",
        "training_eligible": True,
        "provenance_source_ids": ["SRC-RECORD-SWEEP-AUDIT"],
    }
    payload_hash = sha256_text(canonical_json(payload))
    return BrainRecordEnvelope(
        record_id=record_id,
        record_type="supervised_direct_event_case",
        episode_id=episode_id,
        trade_date=date(2030, 1, 9),
        available_from=available_from,
        training_target="direct_event_response",
        evidence_phase="BLIND_SAFE",
        training_eligible=True,
        eligibility_reason="unit test record",
        status="tentative",
        confidence_label="low",
        provenance_source_ids=["SRC-RECORD-SWEEP-AUDIT"],
        raw_payload_sha256=payload_hash,
        normalized_payload_sha256=payload_hash,
        typed_payload_status="KNOWN_TYPED_PAYLOAD",
        source_block="brain_delta.jsonl",
        source_line=1,
        payload=payload,
    )


def _store_brain_records_for_sweep_audit(
    root: Path,
    records: list[BrainRecordEnvelope],
) -> None:
    episode_id = records[0].episode_id
    source_path = root / "synthetic_record_bundle.md"
    raw_payload = "\n".join(record.model_dump_json() for record in records)
    raw_sha = sha256_text(raw_payload)
    source_path.write_text(raw_payload, encoding="utf-8")
    BrainRecordStore(root).store_bundle(
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
            raw_block_hashes={"brain_delta.jsonl": sha256_text(raw_payload)},
            raw_block_counts={"brain_delta.jsonl": len(records)},
            adapter_name="unit-test",
        ),
        index=NormalizedEpisodeIndex(
            episode_id=episode_id,
            trade_date=records[0].trade_date,
            cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
            available_from=min(record.available_from for record in records),
            bundle_status="ACCEPT_FULL",
            blind_valid=True,
            raw_block_names=["brain_delta.jsonl"],
        ),
        records=records,
        raw_blocks={"brain_delta.jsonl": raw_payload},
        validation_report={"passed": True},
        accepted=True,
    )


def _candidate_with_provenance() -> dict[str, object]:
    return {
        "company_name": "CandidateCo",
        "event_ids": ["EVT-1"],
        "provenance": _provenance("test_candidate"),
    }


def _sector_with_provenance() -> dict[str, object]:
    return {
        "name": "SectorCo catalyst cluster",
        "triggering_events": ["EVT-1"],
        "formation_mechanism": "current catalyst -> open-world sector hypothesis",
        "expected_breadth": "narrow",
        "provenance": _provenance("test_dominant_sector"),
    }


def _blind_analysis_with_provenance() -> dict[str, object]:
    return {
        "summary": "Test blind analysis.",
        "open_world_mechanisms": ["current catalyst -> open-world mechanism"],
        "provenance": _provenance("test_blind_analysis"),
    }


def _sealed_prediction_payload(*, context_manifest_id: str = "RUN-linked") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "nslab.blind_prediction.v1",
        "prediction_id": "PRED-sealed",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "created_at": "2030-01-10T08:59:00+09:00",
        "sealed_at": "2030-01-10T08:59:30+09:00",
        "blind_artifact_sha256": None,
        "context_manifest_id": context_manifest_id,
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    payload["blind_artifact_sha256"] = sha256_text(canonical_json(payload))
    return payload


def _preopen_report_text(
    *,
    run_id: str = "RUN-linked",
    omit_heading: str | None = None,
    empty_heading: str | None = None,
) -> str:
    lines = [
        "# Pre-Open Research Report: 2030-01-10",
        "",
        f"- Run ID: `{run_id}`",
    ]
    for heading in PREOPEN_REPORT_SECTION_HEADINGS:
        if heading == omit_heading:
            continue
        section_body = "" if heading == empty_heading else "section body"
        lines.extend(["", heading, "", section_body])
    return "\n".join(lines) + "\n"


def _trace_payload(
    *,
    prompt_sha256: str = "blind-hash",
    purpose: str = "daily_blind_analysis",
    response_model: str = "BlindPrediction",
    prompt_version: str = "daily_blind_analysis.v1",
    model_config: dict[str, object] | None = None,
    output: dict[str, object] | None = None,
    checkpoint_id: str = "LLMCKPT-linked",
    trace_id: str = "TRACE-linked",
) -> dict[str, object]:
    trace_input = {
        "prompt_sha256": prompt_sha256,
        "prompt_chars": 100,
        "response_model": response_model,
    }
    trace_output = output or {"prediction_id": "PRED-linked"}
    return {
        "schema_version": "nslab.llm_trace.v1",
        "trace_id": trace_id,
        "operation": "generate_structured",
        "purpose": purpose,
        "status": "ok",
        "provider": "DeterministicMockLLMProvider",
        "model_config": model_config or {"provider": "mock"},
        "metadata": {"prompt_version": prompt_version},
        "input": trace_input,
        "input_sha256": sha256_text(canonical_json(trace_input)),
        "output": trace_output,
        "output_sha256": sha256_text(canonical_json(trace_output)),
        "checkpoint_id": checkpoint_id,
        "tool_calls": [],
        "retries": 0,
        "retry_errors": [],
        "token_usage": {"prompt_tokens_estimate": 25, "completion_tokens_estimate": 10},
        "started_at": "2030-01-10T08:59:00+09:00",
        "finished_at": "2030-01-10T08:59:01+09:00",
        "prompt_version": prompt_version,
    }


def _write_trace_checkpoint(root: Path, trace_payload: dict[str, object]) -> None:
    checkpoint_id = trace_payload.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise AssertionError("test trace payload missing checkpoint_id")
    checkpoint_dir = root / "runs" / "checkpoints" / "llm"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    trace_status = trace_payload.get("status")
    checkpoint_status = "ok" if trace_status == "checkpoint_hit" else trace_status
    write_json(
        checkpoint_dir / f"{checkpoint_id}.json",
        {
            "checkpoint_id": checkpoint_id,
            "schema_version": "nslab.llm_checkpoint.v1",
            "operation": trace_payload.get("operation"),
            "purpose": trace_payload.get("purpose"),
            "status": checkpoint_status,
            "provider": trace_payload.get("provider"),
            "model_config": trace_payload.get("model_config"),
            "metadata": trace_payload.get("metadata"),
            "input": trace_payload.get("input"),
            "input_sha256": trace_payload.get("input_sha256"),
            "output": trace_payload.get("output"),
            "output_sha256": trace_payload.get("output_sha256"),
            "token_usage": trace_payload.get("token_usage"),
            "retries": trace_payload.get("retries"),
            "retry_errors": trace_payload.get("retry_errors"),
            "updated_at": "2030-01-10T08:59:01+09:00",
        },
    )


def _manifest_reproducibility_fields() -> dict[str, object]:
    return {
        "schema_version": "nslab.context_manifest.v1",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "accepted_episode_count": 0,
        "total_accepted_episode_count": 0,
        "available_episode_count": 0,
        "unavailable_episode_count": 0,
        "unavailable_episode_ids": [],
        "model_config": {"provider": "mock"},
        "token_counts": {"current_news": 1},
        "truncations": [],
        "web_queries": [],
        "web_sources": [],
    }


class RecordingPriceSource:
    source_name = "recording-price"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, date]] = []

    def get_history(self, ticker: str, *, through: date) -> list[PriceRecord]:
        self.calls.append(("history", ticker, through))
        return [PriceRecord(ticker=ticker, trade_date=through, close=1000.0)]

    def get_snapshot(self, ticker: str, *, as_of: date) -> PriceRecord | None:
        self.calls.append(("snapshot", ticker, as_of))
        return PriceRecord(ticker=ticker, trade_date=as_of, close=1000.0)

    def get_outcome(self, ticker: str, *, trade_date: date) -> NoReturn:
        self.calls.append(("outcome", ticker, trade_date))
        raise AssertionError("blind guard must not delegate outcome access")

    def get_outcome_universe(self, *, trade_date: date) -> dict[str, OutcomeLabels]:
        self.calls.append(("outcome_universe", "*", trade_date))
        raise AssertionError("blind guard must not delegate outcome universe access")


def test_blind_price_guard_blocks_d_day() -> None:
    trade_day = date(2030, 1, 10)
    prior_day = date(2030, 1, 9)
    future_day = date(2030, 1, 11)
    source = RecordingPriceSource()
    guard = BlindPriceGuard(source, trade_date=trade_day)

    assert guard.source_name == "recording-price"
    assert guard.get_snapshot("UNKNOWN", as_of=prior_day) is not None
    assert guard.get_history("UNKNOWN", through=prior_day)
    assert source.calls == [
        ("snapshot", "UNKNOWN", prior_day),
        ("history", "UNKNOWN", prior_day),
    ]

    with pytest.raises(BlindPriceAccessError):
        guard.get_snapshot("UNKNOWN", as_of=trade_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_history("UNKNOWN", through=trade_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_snapshot("UNKNOWN", as_of=future_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_history("UNKNOWN", through=future_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_outcome("UNKNOWN", trade_date=trade_day)
    with pytest.raises(BlindPriceAccessError):
        guard.get_outcome_universe(trade_date=trade_day)

    assert source.calls == [
        ("snapshot", "UNKNOWN", prior_day),
        ("history", "UNKNOWN", prior_day),
    ]


class FutureOnlyProvider:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-FUTURE",
                title=query,
                url="mock://future",
                snippet="future-only",
                published_at=cutoff_at + timedelta(seconds=1),
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        return url

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return result.published_at is None or result.published_at <= cutoff_at


class ProviderTimestampRejectsUnknown:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-UNVERIFIED",
                title=query,
                url="mock://unknown",
                snippet="timestamp cannot be verified",
                published_at=None,
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        return url

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return False


class ProviderRejectsPreCutoffTimestamp:
    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-REJECTED-PRE-CUTOFF",
                title=query,
                url="mock://rejected-pre-cutoff",
                snippet="provider-level timestamp verification rejects this source",
                published_at=cutoff_at - timedelta(minutes=1),
            )
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        return url

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return False


class MixedTemporalProvider:
    def __init__(self) -> None:
        self.open_calls: list[str] = []

    async def search(self, query: str, *, cutoff_at: datetime) -> list[WebSearchResult]:
        return [
            WebSearchResult(
                source_id="WEB-SAFE",
                title=f"{query} safe",
                url="mock://safe",
                snippet="safe before cutoff",
                published_at=cutoff_at - timedelta(minutes=1),
            ),
            WebSearchResult(
                source_id="WEB-FUTURE",
                title=f"{query} future",
                url="mock://future",
                snippet="future-only",
                published_at=cutoff_at + timedelta(seconds=1),
            ),
        ]

    async def open(self, url: str, *, cutoff_at: datetime) -> str:
        self.open_calls.append(url)
        return url

    async def verify_timestamp(self, result: WebSearchResult, *, cutoff_at: datetime) -> bool:
        return result.published_at is not None and result.published_at <= cutoff_at


@pytest.mark.asyncio
async def test_temporal_web_guard_excludes_cutoff_after_sources() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    guard = TemporalWebGuard(FutureOnlyProvider())
    assert await guard.search("query", cutoff_at=cutoff) == []
    assert guard.excluded_source_ids == ["WEB-FUTURE"]
    assert guard.excluded_sources[0].reason == "published_after_cutoff"
    assert guard.excluded_sources[0].result.source_id == "WEB-FUTURE"


@pytest.mark.asyncio
async def test_temporal_web_guard_uses_provider_timestamp_verification() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    guard = TemporalWebGuard(ProviderTimestampRejectsUnknown())
    assert await guard.search("query", cutoff_at=cutoff) == []
    assert guard.excluded_source_ids == ["WEB-UNVERIFIED"]
    assert guard.excluded_sources[0].reason == "missing_published_at"


@pytest.mark.asyncio
async def test_temporal_web_guard_rejects_provider_failed_timestamp_verification() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    guard = TemporalWebGuard(ProviderRejectsPreCutoffTimestamp())

    assert await guard.search("query", cutoff_at=cutoff) == []
    assert guard.excluded_source_ids == ["WEB-REJECTED-PRE-CUTOFF"]
    assert guard.excluded_sources[0].reason == "timestamp_verification_failed"


@pytest.mark.asyncio
async def test_temporal_web_guard_only_opens_search_accepted_sources() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    provider = MixedTemporalProvider()
    guard = TemporalWebGuard(provider)

    kept = await guard.search("query", cutoff_at=cutoff)

    assert [result.source_id for result in kept] == ["WEB-SAFE"]
    assert await guard.open("mock://safe", cutoff_at=cutoff) == "mock://safe"
    with pytest.raises(ValueError, match="unverified web source"):
        await guard.open("mock://unseen", cutoff_at=cutoff)
    with pytest.raises(ValueError, match="unverified web source"):
        await guard.open("mock://future", cutoff_at=cutoff)
    assert provider.open_calls == ["mock://safe"]


@pytest.mark.asyncio
async def test_temporal_web_guard_rechecks_cutoff_before_opening() -> None:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    earlier_cutoff = cutoff - timedelta(minutes=2)
    provider = MixedTemporalProvider()
    guard = TemporalWebGuard(provider)

    kept = await guard.search("query", cutoff_at=cutoff)

    assert [result.source_id for result in kept] == ["WEB-SAFE"]
    with pytest.raises(ValueError, match="cutoff-unsafe web source"):
        await guard.open("mock://safe", cutoff_at=earlier_cutoff)
    assert provider.open_calls == []


def test_hardcoding_audit_passes_current_source() -> None:
    root = Path(__file__).resolve().parents[2]
    result = audit_hardcoding(root)
    assert result["passed"], result["findings"]


def test_hardcoding_audit_flags_known_company_literals_from_memory(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    company_dir = tmp_path / "memory" / "company_memory"
    company_dir.mkdir(parents=True)
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True)
    write_json(
        company_dir / "CM-alpha.json",
        {
            "company_name": "FictionalAlpha Holdings",
            "aliases": ["FictionalAlpha"],
        },
    )
    write_json(
        accepted_dir / "EP-company.json",
        {
            "blind_predictions": [
                {"company_name": "FictionalBeta Systems"},
            ],
            "event_ticker_edges": [
                {"company_name": "FictionalGamma Parts"},
            ],
        },
    )
    (source_dir / "leaked_company_knowledge.py").write_text(
        """
DEFAULT_LEADER = "FictionalAlpha Holdings"
def fallback_candidate() -> str:
    return "FictionalBeta Systems"
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    company_findings = [
        finding
        for finding in findings
        if finding["rule"] == "known_company_name_literal"
    ]
    assert {finding["match"] for finding in company_findings} == {
        "FictionalAlpha Holdings",
        "FictionalBeta Systems",
    }


def test_hardcoding_audit_flags_domain_maps_and_ticker_lists(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "domain_rules.py").write_text(
        """
THEME_MAP = {
    "new_policy": ["111111", "FictionalBuilder"],
}

BENEFICIARY_WHITELIST = ["222222", "FictionalInfra"]
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    rules = {finding["rule"] for finding in findings}
    assert "domain_hardcoding_collection" in rules
    assert "quoted_six_digit_ticker" in rules


def test_hardcoding_audit_flags_constructor_maps_and_numeric_tickers(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "constructor_rules.py").write_text(
        """
TICKERS = [111111, 222222]
THEME_TO_STOCKS = dict(new_policy=[333333])
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    rules = {finding["rule"] for finding in findings}
    assert "numeric_six_digit_ticker" in rules
    assert "domain_hardcoding_collection" in rules


def test_hardcoding_audit_flags_hangul_candidate_collections(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "candidate_lists.py").write_text(
        """
CANDIDATES = list(["가상전자", "샘플모빌리티"])
SECTOR_BENEFICIARIES = {
    "가상첨단산업": ["샘플전력", "예시 장비"]
}
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert {finding["rule"] for finding in findings} == {
        "hangul_domain_literal_collection"
    }


def test_hardcoding_audit_flags_actual_hangul_domain_literals(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "candidate_lists.py").write_text(
        "\n".join(
            [
                r'CANDIDATES = list(["\uac74\uc124", "\ubc18\ub3c4\uccb4"])',
                (
                    r'SECTOR_BENEFICIARIES = {"\uc9c0\uc5ed \ud14c\ub9c8": '
                    r'["\ubb3c\ub958"]}'
                ),
            ]
        ),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert {finding["rule"] for finding in findings} == {
        "hangul_domain_literal_collection"
    }


def test_hardcoding_audit_flags_fixed_news_expression_scores(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "ranking.py").write_text(
        """
def rank(title: str) -> int:
    score = 0
    if "special-region plant" in title:
        score = 5
    return score
""".strip(),
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    rules = {finding["rule"] for finding in findings}
    assert "fixed_expression_score" in rules


def test_hardcoding_audit_scans_prompts_and_repo_guidance(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts" / "blind_analysis"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "open_world.md").write_text(
        'ticker_list: ["111111"]\n',
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text(
        "theme_map: {policy: ['FictionalCo']}\n",
        encoding="utf-8",
    )
    skill_dir = tmp_path / ".agents" / "skills" / "news-scalping-lab"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        'beneficiary_whitelist = {"fictional": ["222222"]}\n',
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    rules = {finding["rule"] for finding in findings}
    assert "guidance_six_digit_ticker" in rules
    assert "guidance_domain_collection" in rules


def test_hardcoding_audit_flags_hangul_collections_in_prompts(tmp_path: Path) -> None:
    prompt_dir = tmp_path / "prompts" / "blind_analysis"
    prompt_dir.mkdir(parents=True)
    (prompt_dir / "regional_rules.md").write_text(
        '\uc9c0\uc5ed_\ud14c\ub9c8 = ["\uac74\uc124", "\ubb3c\ub958"]\n',
        encoding="utf-8",
    )

    result = audit_hardcoding(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert {finding["rule"] for finding in findings} == {
        "guidance_hangul_domain_collection"
    }


def test_provenance_audit_requires_prediction_context_manifest(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(run_id="RUN-missing"), encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "2030-01-10.json: missing context_manifest_id" in result["findings"]


def test_provenance_audit_accepts_manifest_and_report_links(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = _sealed_prediction_payload()
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        prediction,
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    write_json(run_prediction_path, prediction)
    run_report_path.write_text(_preopen_report_text(), encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": prediction["blind_artifact_sha256"],
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
            "report_artifact": run_report_path.relative_to(tmp_path).as_posix(),
            "report_sha256": sha256_text(run_report_path.read_text(encoding="utf-8")),
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]


def test_provenance_audit_validates_context_manifest_reproducibility_fields(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    manifest = {
        **_manifest_reproducibility_fields(),
        "run_id": "RUN-linked",
        "prompt_hashes": {"blind_analysis": "def456"},
        "token_counts": {"current_news": 1, "blind_analysis_prompt": 25},
        "price_snapshot": {"allowed_through": "2030-01-09"},
        "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
    }
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(manifest_path, manifest)
    trace_payload = _trace_payload(prompt_sha256="def456")
    write_json(
        tmp_path / "runs" / "traces" / "TRACE-daily.json",
        trace_payload,
    )
    _write_trace_checkpoint(tmp_path, trace_payload)

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_manifest = {
        **manifest,
        "token_counts": {"current_news": "1"},
        "truncations": ["ok", 1],
        "web_queries": "not-a-list",
        "web_sources": [1],
        "total_accepted_episode_count": 1,
        "available_episode_count": 1,
        "unavailable_episode_ids": ["EP-missing"],
    }
    del bad_manifest["model_config"]
    write_json(manifest_path, bad_manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert "2030-01-10.json: context manifest missing model_config" in findings
    assert "2030-01-10.json: context manifest token_counts is invalid" in findings
    assert "2030-01-10.json: context manifest truncations is invalid" in findings
    assert "2030-01-10.json: context manifest web_queries is invalid" in findings
    assert "2030-01-10.json: context manifest web_sources is invalid" in findings
    assert (
        "2030-01-10.json: context manifest episode scope "
        "total_accepted_episode_count_mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest episode scope "
        "available_episode_count_mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest episode scope "
        "unavailable_episode_ids_mismatch"
    ) in findings


def test_provenance_audit_requires_current_manifest_artifact_contract(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = _sealed_prediction_payload()
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            **_manifest_reproducibility_fields(),
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": prediction["blind_artifact_sha256"],
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "2030-01-10.json: context manifest missing news_file" in findings
    assert "2030-01-10.json: context manifest missing prediction_artifact" in findings
    assert "2030-01-10.json: context manifest missing report_artifact" in findings
    assert (
        "2030-01-10.json: context manifest missing final_synthesis_context_artifact"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest red_team_artifacts is invalid"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest missing semantic_retrieval_plan prompt hash"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest missing final_synthesis prompt hash"
        in findings
    )


def test_provenance_audit_validates_manifest_news_input_hash(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    news_path = tmp_path / "data" / "inbox" / "news" / "20300110.csv"
    news_path.parent.mkdir(parents=True)
    news_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","NewsCo, catalyst","Original input."\n'
        '1,2,"2030-01-10","09:30:00","After cutoff","Excluded input."\n',
        encoding="utf-8",
    )
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "news_file": news_path.relative_to(tmp_path).as_posix(),
            "news_sha256": file_sha256(news_path),
            "news_window_start_at": "2030-01-09T15:30:00+09:00",
            "news_window_end_at": "2030-01-10T08:59:59+09:00",
            "news_row_count": 2,
            "included_news_row_count": 1,
            "excluded_news_row_count": 1,
            "row_disposition_summary": {"missing_collected_at": 2},
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    manifest = read_json(manifest_path)
    manifest["included_news_row_count"] = 2
    manifest["excluded_news_row_count"] = 0
    write_json(manifest_path, manifest)

    failed_counts = audit_provenance(tmp_path)

    assert not failed_counts["passed"]
    assert (
        "2030-01-10.json: context manifest included_news_row_count mismatch"
        in failed_counts["findings"]
    )
    assert (
        "2030-01-10.json: context manifest excluded_news_row_count mismatch"
        in failed_counts["findings"]
    )

    manifest["included_news_row_count"] = 1
    manifest["excluded_news_row_count"] = 1
    write_json(manifest_path, manifest)

    news_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","NewsCo, catalyst","Tampered input."\n',
        encoding="utf-8",
    )

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    assert "2030-01-10.json: context manifest news_sha256 mismatch" in failed["findings"]


def test_provenance_audit_validates_manifest_output_artifacts(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    bad_run_prediction = {**prediction, "context_manifest_id": "RUN-other"}
    write_json(run_prediction_path, bad_run_prediction)
    run_report_path.write_text(_preopen_report_text(run_id="RUN-other"), encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": "0" * 64,
            "report_artifact": run_report_path.relative_to(tmp_path).as_posix(),
            "report_sha256": "1" * 64,
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "2030-01-10.json: context manifest prediction_sha256 mismatch" in findings
    assert (
        "2030-01-10.json: context manifest prediction_artifact run_id mismatch"
        in findings
    )
    assert "2030-01-10.json: context manifest report_sha256 mismatch" in findings
    assert "2030-01-10.json: context manifest report_artifact missing run id" in findings


def test_provenance_audit_validates_manifest_supporting_jsonl_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    row_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "row_disposition"
        / "RUN-linked"
        / "row_disposition.jsonl"
    )
    source_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "source_ledger"
        / "RUN-linked"
        / "source_ledger.jsonl"
    )
    row_path.parent.mkdir(parents=True)
    source_path.parent.mkdir(parents=True)
    row_text = (
        canonical_json(
            {
                "schema_version": "nslab.row_disposition.v1",
                "run_id": "RUN-linked",
                "row_number": 1,
            }
        )
        + "\n"
    )
    source_text = (
        canonical_json(
            {
                "schema_version": "nslab.source_ledger.v1",
                "run_id": "RUN-linked",
                "source_id": "SRC-news",
                "source_type": "news_csv_row",
            }
        )
        + "\n"
    )
    row_path.write_text(row_text, encoding="utf-8")
    source_path.write_text(source_text, encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "row_disposition_artifact": row_path.relative_to(tmp_path).as_posix(),
            "row_disposition_sha256": sha256_text(row_text),
            "row_disposition_summary": {"total_rows": 1},
            "source_ledger_artifact": source_path.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": sha256_text(source_text),
            "source_ledger_entry_count": 1,
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_row_text = (
        canonical_json(
            {
                "schema_version": "bad.row",
                "run_id": "RUN-other",
                "row_number": 1,
            }
        )
        + "\n"
        + "not-json\n"
    )
    bad_source_text = (
        canonical_json(
            {
                "schema_version": "bad.source",
                "run_id": "RUN-other",
                "source_id": "SRC-news",
            }
        )
        + "\n"
    )
    row_path.write_text(bad_row_text, encoding="utf-8")
    source_path.write_text(bad_source_text, encoding="utf-8")
    manifest = read_json(manifest_path)
    manifest["row_disposition_sha256"] = "0" * 64
    manifest["row_disposition_summary"] = {"total_rows": 2}
    manifest["source_ledger_sha256"] = "1" * 64
    manifest["source_ledger_entry_count"] = 2
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert "2030-01-10.json: context manifest row_disposition_sha256 mismatch" in findings
    assert (
        "2030-01-10.json: context manifest row_disposition:1 schema_version mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest row_disposition:1 run_id mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest row_disposition:2 invalid JSON"
        in findings
    )
    assert "2030-01-10.json: context manifest row_disposition count mismatch" in findings
    assert "2030-01-10.json: context manifest source_ledger_sha256 mismatch" in findings
    assert (
        "2030-01-10.json: context manifest source_ledger:1 schema_version mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest source_ledger:1 run_id mismatch"
        in findings
    )
    assert "2030-01-10.json: context manifest source_ledger count mismatch" in findings


def test_provenance_audit_validates_source_ledger_manifest_source_coverage(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-ledger",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(run_id="RUN-ledger"), encoding="utf-8"
    )
    source_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "source_ledger"
        / "RUN-ledger"
        / "source_ledger.jsonl"
    )
    source_path.parent.mkdir(parents=True)

    def ledger_row(source_id: str, source_type: str) -> dict[str, str]:
        return {
            "schema_version": "nslab.source_ledger.v1",
            "run_id": "RUN-ledger",
            "source_id": source_id,
            "source_type": source_type,
        }

    source_rows = [
        ledger_row("WEB-A", "web_search_result"),
        ledger_row("WEB-B", "web_search_result"),
        ledger_row("WEB-C", "candidate_web_check"),
    ]
    source_text = "".join(canonical_json(row) + "\n" for row in source_rows)
    source_path.write_text(source_text, encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-ledger.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-ledger",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "source_ledger_artifact": source_path.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": sha256_text(source_text),
            "source_ledger_entry_count": len(source_rows),
            "source_ledger_summary": {
                "total_sources": len(source_rows),
                "blind_sources": 0,
                "outcome_sources": 0,
                "postmortem_sources": 0,
            },
            "web_sources": ["WEB-B", "WEB-A"],
            "candidate_web_source_ids": ["WEB-C"],
            "excluded_web_source_ids": ["WEB-X"],
            "excluded_candidate_web_source_ids": [],
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_source_rows = [
        ledger_row("WEB-A", "web_search_result"),
        ledger_row("WEB-X", "web_search_result"),
    ]
    bad_source_text = "".join(canonical_json(row) + "\n" for row in bad_source_rows)
    source_path.write_text(bad_source_text, encoding="utf-8")
    manifest = read_json(manifest_path)
    manifest["source_ledger_sha256"] = sha256_text(bad_source_text)
    manifest["source_ledger_entry_count"] = len(bad_source_rows)
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: context manifest source_ledger web_sources mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest source_ledger_summary mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest source_ledger "
        "candidate_web_source_ids mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest source_ledger contains excluded source_id"
        in findings
    )


def test_provenance_audit_validates_event_cluster_and_novelty_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    event_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "event_clusters"
        / "RUN-linked"
        / "event_clusters.jsonl"
    )
    novelty_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "news_novelty_reviews"
        / "RUN-linked"
        / "news_novelty_review.json"
    )
    event_path.parent.mkdir(parents=True)
    novelty_path.parent.mkdir(parents=True)
    event_text = (
        canonical_json(
            {
                "schema_version": "nslab.news_event_cluster.v1",
                "run_id": "RUN-linked",
                "cluster_id": "EVCL-1",
                "cluster_index": 1,
                "cluster_method": "exact_test_v1",
                "row_numbers": [1, 2],
                "event_ids": ["EVT-1", "EVT-2"],
                "source_ids": ["SRC-1", "SRC-2"],
                "row_count": 2,
                "exact_duplicate_count": 1,
            }
        )
        + "\n"
    )
    event_path.write_text(event_text, encoding="utf-8")
    novelty_payload = {
        "schema_version": "nslab.news_novelty_review.v1",
        "run_id": "RUN-linked",
        "prompt_sha256": "novelty-hash",
        "cluster_count": 1,
        "reviewed_cluster_count": 1,
        "findings": [
            {
                "cluster_id": "EVCL-1",
                "cluster_index": 1,
                "novelty": "new",
                "time_verified": True,
            }
        ],
        "excluded_after_cutoff_source_ids": [],
    }
    write_json(novelty_path, novelty_payload)
    novelty_text = novelty_path.read_text(encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {
                "blind_analysis": "def456",
                "news_novelty_review": "novelty-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "event_cluster_artifact": event_path.relative_to(tmp_path).as_posix(),
            "event_cluster_sha256": sha256_text(event_text),
            "event_cluster_count": 1,
            "event_cluster_summary": {
                "cluster_count": 1,
                "source_row_count": 2,
                "exact_duplicate_count": 1,
                "exact_duplicate_cluster_count": 1,
                "cluster_method": "exact_test_v1",
            },
            "news_novelty_review_artifact": novelty_path.relative_to(tmp_path).as_posix(),
            "news_novelty_review_sha256": sha256_text(novelty_text),
            "news_novelty_review_count": 1,
            "news_novelty_review_summary": {
                "cluster_count": 1,
                "reviewed_cluster_count": 1,
                "novelty_counts": {"new": 1, "unclear": 0},
                "time_verified_count": 1,
                "excluded_after_cutoff_source_count": 0,
            },
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_event_text = (
        canonical_json(
            {
                "schema_version": "bad.event",
                "run_id": "RUN-other",
                "cluster_id": "EVCL-1",
                "cluster_index": 1,
                "cluster_method": "exact_test_v1",
                "row_numbers": [1],
                "event_ids": ["EVT-1"],
                "source_ids": ["SRC-1"],
                "row_count": 1,
                "exact_duplicate_count": 0,
            }
        )
        + "\n"
    )
    event_path.write_text(bad_event_text, encoding="utf-8")
    write_json(
        novelty_path,
        {
            **novelty_payload,
            "schema_version": "bad.review",
            "run_id": "RUN-other",
            "prompt_sha256": "bad-prompt",
            "cluster_count": 2,
            "reviewed_cluster_count": 2,
            "findings": [
                {
                    "cluster_id": "EVCL-1",
                    "cluster_index": 1,
                    "novelty": "unclear",
                    "time_verified": False,
                }
            ],
            "excluded_after_cutoff_source_ids": ["SRC-after"],
        },
    )
    manifest = read_json(manifest_path)
    manifest["event_cluster_sha256"] = "0" * 64
    manifest["event_cluster_count"] = 2
    manifest["news_novelty_review_sha256"] = "1" * 64
    manifest["news_novelty_review_count"] = 2
    manifest["news_novelty_review_summary"] = {
        "cluster_count": 1,
        "reviewed_cluster_count": 2,
        "novelty_counts": {"new": 1},
        "time_verified_count": 1,
        "excluded_after_cutoff_source_count": 0,
    }
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert "2030-01-10.json: context manifest event_cluster_sha256 mismatch" in findings
    assert (
        "2030-01-10.json: context manifest event_cluster:1 schema_version mismatch"
        in findings
    )
    assert "2030-01-10.json: context manifest event_cluster:1 run_id mismatch" in findings
    assert "2030-01-10.json: context manifest event_cluster count mismatch" in findings
    assert (
        "2030-01-10.json: context manifest event_cluster source_row_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest event_cluster exact_duplicate_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest news_novelty_review_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest news_novelty_review schema_version mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest news_novelty_review run_id mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest news_novelty_review prompt_hash mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest news_novelty_review count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest news_novelty_review "
        "reviewed_cluster_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest news_novelty_review novelty_counts mismatch"
        in findings
    )


def test_provenance_audit_validates_semantic_retrieval_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    retrieval_dir = tmp_path / "runs" / "checkpoints" / "semantic_retrieval" / "RUN-linked"
    retrieval_dir.mkdir(parents=True)
    plan_path = retrieval_dir / "semantic_retrieval_plan.json"
    result_path = retrieval_dir / "semantic_retrieval.jsonl"
    categories = ["positive_analogs", "negative_controls"]
    plan_payload = {
        "schema_version": "nslab.semantic_retrieval_plan.v1",
        "run_id": "RUN-linked",
        "prompt_sha256": "semantic-plan-hash",
        "required_categories": categories,
        "queries": [
            {
                "category": "positive_analogs",
                "query": "positive structural analog",
                "rationale": "positive cases",
            },
            {
                "category": "negative_controls",
                "query": "negative control structural analog",
                "rationale": "negative cases",
            },
        ],
    }
    write_json(plan_path, plan_payload)
    result_rows = [
        {
            "schema_version": "nslab.semantic_retrieval_result.v1",
            "run_id": "RUN-linked",
            "query_index": 1,
            "category": "positive_analogs",
            "query": "positive structural analog",
            "query_sha256": sha256_text("positive structural analog"),
            "included_episode_ids": ["EP-1"],
            "excluded_episode_ids": [],
            "result_count": 1,
            "excluded_count": 0,
        },
        {
            "schema_version": "nslab.semantic_retrieval_result.v1",
            "run_id": "RUN-linked",
            "query_index": 2,
            "category": "negative_controls",
            "query": "negative control structural analog",
            "query_sha256": sha256_text("negative control structural analog"),
            "included_episode_ids": [],
            "excluded_episode_ids": ["EP-2"],
            "result_count": 0,
            "excluded_count": 1,
        },
    ]
    result_text = "".join(canonical_json(row) + "\n" for row in result_rows)
    result_path.write_text(result_text, encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {
                "blind_analysis": "def456",
                "semantic_retrieval_plan": "semantic-plan-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "semantic_retrieval_plan_artifact": plan_path.relative_to(tmp_path).as_posix(),
            "semantic_retrieval_plan_sha256": sha256_text(
                plan_path.read_text(encoding="utf-8")
            ),
            "semantic_retrieval_artifact": result_path.relative_to(tmp_path).as_posix(),
            "semantic_retrieval_sha256": sha256_text(result_text),
            "semantic_retrieval_query_count": 2,
            "semantic_retrieval_episode_ids": ["EP-1"],
            "excluded_semantic_retrieval_episode_ids": ["EP-2"],
            "semantic_retrieval_summary": {
                "required_categories": categories,
                "category_query_counts": {
                    "positive_analogs": 1,
                    "negative_controls": 1,
                },
                "query_count": 2,
                "included_episode_count": 1,
                "excluded_episode_count": 1,
                "retrieval_zero_is_valid": True,
            },
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    write_json(
        plan_path,
        {
            **plan_payload,
            "schema_version": "bad.plan",
            "run_id": "RUN-other",
            "prompt_sha256": "wrong-prompt",
            "required_categories": ["wrong_category"],
            "queries": [{"category": "wrong_category", "query": "", "rationale": ""}],
        },
    )
    bad_rows = [
        {
            **result_rows[0],
            "schema_version": "bad.result",
            "run_id": "RUN-other",
            "query_sha256": "bad-sha",
            "included_episode_ids": ["EP-X"],
            "excluded_episode_ids": ["EP-Y"],
            "result_count": 2,
            "excluded_count": 2,
        }
    ]
    bad_result_text = "".join(canonical_json(row) + "\n" for row in bad_rows)
    result_path.write_text(bad_result_text, encoding="utf-8")
    manifest = read_json(manifest_path)
    manifest["semantic_retrieval_plan_sha256"] = "0" * 64
    manifest["semantic_retrieval_sha256"] = "1" * 64
    manifest["semantic_retrieval_query_count"] = 2
    manifest["semantic_retrieval_summary"] = {
        "required_categories": categories,
        "category_query_counts": {
            "positive_analogs": 1,
            "negative_controls": 1,
        },
        "query_count": 2,
        "included_episode_count": 1,
        "excluded_episode_count": 1,
        "retrieval_zero_is_valid": False,
    }
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan schema_version mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan run_id mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan prompt_hash mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan required_categories mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan query_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan category coverage mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_plan query text invalid"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval:1 schema_version mismatch"
        in findings
    )
    assert "2030-01-10.json: context manifest semantic_retrieval:1 run_id mismatch" in findings
    assert "2030-01-10.json: context manifest semantic_retrieval count mismatch" in findings
    assert (
        "2030-01-10.json: context manifest semantic_retrieval category_counts mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval included_episode_ids mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval excluded_episode_ids mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval zero_policy missing"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval category coverage mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval:1 query_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval:1 result_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest semantic_retrieval:1 excluded_count mismatch"
        in findings
    )


def test_provenance_audit_validates_candidate_expansion_artifact(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    artifact_dir = tmp_path / "runs" / "checkpoints" / "candidate_expansion" / "RUN-linked"
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "candidate_expansion.json"
    required_paths = ["SINGLE_EVENT", "CONTINUATION"]
    payload = {
        "schema_version": "nslab.candidate_expansion.v1",
        "run_id": "RUN-linked",
        "prompt_sha256": "candidate-expansion-hash",
        "required_paths": required_paths,
        "findings": [
            {
                "path": "SINGLE_EVENT",
                "hypothesis": "Direct catalyst route.",
                "candidate_names": ["DirectCandidate"],
                "sector_hypotheses": ["direct sector"],
                "investigation_questions": ["verify directness"],
                "evidence_source_ids": ["SRC-1"],
                "related_cluster_ids": ["EVCL-1"],
                "memory_episode_ids": [],
                "requires_web_company_discovery": True,
                "d_minus_one_market_data_only": False,
                "uncertainties": ["needs web check"],
            },
            {
                "path": "CONTINUATION",
                "hypothesis": "D-1 continuation route.",
                "candidate_names": ["ContinuationCandidate"],
                "sector_hypotheses": ["continuation sector"],
                "investigation_questions": ["verify D-1 only"],
                "evidence_source_ids": [],
                "related_cluster_ids": ["EVCL-2"],
                "memory_episode_ids": ["EP-1"],
                "requires_web_company_discovery": False,
                "d_minus_one_market_data_only": True,
                "uncertainties": [],
            },
        ],
    }
    write_json(artifact_path, payload)
    artifact_text = artifact_path.read_text(encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {
                "blind_analysis": "def456",
                "candidate_expansion": "candidate-expansion-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "candidate_expansion_artifact": artifact_path.relative_to(tmp_path).as_posix(),
            "candidate_expansion_sha256": sha256_text(artifact_text),
            "candidate_expansion_count": 2,
            "candidate_expansion_summary": {
                "required_paths": required_paths,
                "path_counts": {"SINGLE_EVENT": 1, "CONTINUATION": 1},
                "finding_count": 2,
                "candidate_name_count": 2,
                "requires_web_company_discovery_count": 1,
                "continuation_d_minus_one_only_verified": True,
            },
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    retrieval_miss_manifest = {
        **read_json(manifest_path),
        "semantic_retrieval_episode_ids": [],
        "semantic_retrieval_summary": {
            "category_query_counts": {},
            "query_count": 0,
            "included_episode_count": 0,
            "excluded_episode_count": 0,
            "retrieval_zero_is_valid": True,
        },
    }
    write_json(manifest_path, retrieval_miss_manifest)
    empty_retrieval_miss_prediction = {
        **prediction,
        "blind_analysis": {
            "summary": "Test blind analysis.",
            "provenance": _provenance("test_blind_analysis"),
        },
        "dominant_sectors": [],
        "candidates": [],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", empty_retrieval_miss_prediction)

    empty_retrieval_result = audit_provenance(tmp_path)

    assert not empty_retrieval_result["passed"]
    assert (
        "2030-01-10.json: retrieval miss missing open-world mechanisms"
        in empty_retrieval_result["findings"]
    )
    assert (
        "2030-01-10.json: retrieval miss produced no candidates"
        in empty_retrieval_result["findings"]
    )
    assert (
        "2030-01-10.json: retrieval miss produced no dominant sectors"
        in empty_retrieval_result["findings"]
    )

    retrieval_miss_prediction = {
        **prediction,
        "blind_analysis": {
            **_blind_analysis_with_provenance(),
            "open_world_mechanisms": ["current catalyst -> open-world review"],
        },
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", retrieval_miss_prediction)
    retrieval_miss_payload = json.loads(json.dumps(payload))
    retrieval_miss_payload["findings"][0]["candidate_names"] = []
    retrieval_miss_payload["findings"][0]["sector_hypotheses"] = []
    retrieval_miss_payload["findings"][0]["investigation_questions"] = []
    retrieval_miss_payload["findings"][0]["requires_web_company_discovery"] = False
    write_json(artifact_path, retrieval_miss_payload)
    retrieval_miss_artifact_text = artifact_path.read_text(encoding="utf-8")
    retrieval_miss_manifest["candidate_expansion_sha256"] = sha256_text(
        retrieval_miss_artifact_text
    )
    retrieval_miss_manifest["candidate_expansion_summary"] = {
        "required_paths": required_paths,
        "path_counts": {"SINGLE_EVENT": 1, "CONTINUATION": 1},
        "finding_count": 2,
        "candidate_name_count": 1,
        "requires_web_company_discovery_count": 0,
        "continuation_d_minus_one_only_verified": True,
    }
    write_json(manifest_path, retrieval_miss_manifest)

    retrieval_miss_artifact_result = audit_provenance(tmp_path)

    assert not retrieval_miss_artifact_result["passed"]
    retrieval_findings = retrieval_miss_artifact_result["findings"]
    assert (
        "2030-01-10.json: retrieval miss missing web company discovery plan"
        in retrieval_findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "retrieval miss candidate_names empty"
    ) in retrieval_findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "retrieval miss sector_hypotheses empty"
    ) in retrieval_findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "retrieval miss investigation_questions empty"
    ) in retrieval_findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "retrieval miss web discovery missing"
    ) in retrieval_findings

    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    write_json(artifact_path, payload)
    write_json(manifest_path, read_json(manifest_path) | {
        "candidate_expansion_sha256": sha256_text(artifact_text),
        "candidate_expansion_summary": {
            "required_paths": required_paths,
            "path_counts": {"SINGLE_EVENT": 1, "CONTINUATION": 1},
            "finding_count": 2,
            "candidate_name_count": 2,
            "requires_web_company_discovery_count": 1,
            "continuation_d_minus_one_only_verified": True,
        },
    })

    bad_payload = {
        **payload,
        "schema_version": "bad.candidate_expansion",
        "run_id": "RUN-other",
        "prompt_sha256": "bad-prompt",
        "required_paths": ["SINGLE_EVENT"],
        "findings": [
            {
                "path": "SINGLE_EVENT",
                "hypothesis": "",
                "candidate_names": ["DirectCandidate", 1],
                "sector_hypotheses": ["direct sector"],
                "investigation_questions": "not-a-list",
                "evidence_source_ids": [],
                "related_cluster_ids": [],
                "memory_episode_ids": [],
                "requires_web_company_discovery": "yes",
                "d_minus_one_market_data_only": "no",
                "uncertainties": [],
            }
        ],
    }
    write_json(artifact_path, bad_payload)
    manifest = read_json(manifest_path)
    manifest["candidate_expansion_sha256"] = "0" * 64
    manifest["candidate_expansion_count"] = 2
    manifest["candidate_expansion_summary"] = {
        "required_paths": required_paths,
        "path_counts": {"SINGLE_EVENT": 1, "CONTINUATION": 1},
        "finding_count": 2,
        "candidate_name_count": 2,
        "requires_web_company_discovery_count": 1,
        "continuation_d_minus_one_only_verified": True,
    }
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: context manifest candidate_expansion_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion schema_version mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion run_id mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion prompt_hash mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion required_paths mismatch"
        in findings
    )
    assert "2030-01-10.json: context manifest candidate_expansion finding_count mismatch" in findings
    assert "2030-01-10.json: context manifest candidate_expansion count mismatch" in findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion path coverage mismatch"
        in findings
    )
    assert "2030-01-10.json: context manifest candidate_expansion path_counts mismatch" in findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion candidate_name_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion "
        "requires_web_company_discovery_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion "
        "continuation_d_minus_one mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 candidate_names invalid"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "investigation_questions invalid"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 hypothesis invalid"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "requires_web_company_discovery invalid"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_expansion:1 "
        "d_minus_one_market_data_only invalid"
    ) in findings


def test_provenance_audit_validates_candidate_web_check_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    artifact_dir = tmp_path / "runs" / "checkpoints" / "candidate_web_checks" / "RUN-linked"
    artifact_dir.mkdir(parents=True)
    candidate_path = artifact_dir / "candidate_web_checks.jsonl"
    excluded_path = artifact_dir / "excluded_candidate_web_checks.jsonl"
    row = {
        "schema_version": "nslab.candidate_web_check.v1",
        "run_id": "RUN-linked",
        "candidate_rank": 1,
        "candidate_ticker": "UNKNOWN",
        "candidate_company_name": "CandidateCo",
        "candidate_path_type": "SINGLE_EVENT",
        "candidate_subject_type": "final_candidate",
        "candidate_expansion_path": None,
        "verification_focus": ["listed_security_and_exact_ticker"],
        "source_id": "WEB-1",
        "title": "candidate source",
        "source_url": "https://example.test/source",
        "url": "https://example.test/source",
        "query": "candidate verification",
        "published_at": "2030-01-10T08:30:00+09:00",
        "retrieved_at": "2030-01-10T08:31:00+09:00",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "time_verified": True,
        "available_before_cutoff": True,
        "content_sha256": "abc",
    }
    candidate_text = canonical_json(row) + "\n"
    candidate_path.write_text(candidate_text, encoding="utf-8")
    excluded_row = {
        "schema_version": "nslab.excluded_candidate_web_check.v1",
        "run_id": "RUN-linked",
        "candidate_rank": 1,
        "candidate_ticker": "UNKNOWN",
        "candidate_company_name": "CandidateCo",
        "candidate_path_type": "SINGLE_EVENT",
        "candidate_subject_type": "final_candidate",
        "candidate_expansion_path": None,
        "source_id": "WEB-X",
        "title": "excluded candidate source",
        "source_url": "https://example.test/excluded",
        "url": "https://example.test/excluded",
        "query": "candidate verification",
        "published_at": "2030-01-10T09:30:00+09:00",
        "retrieved_at": "2030-01-10T09:31:00+09:00",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "exclusion_reason": "after_cutoff",
    }
    excluded_text = canonical_json(excluded_row) + "\n"
    excluded_path.write_text(excluded_text, encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "candidate_web_check_artifact": candidate_path.relative_to(tmp_path).as_posix(),
            "candidate_web_check_sha256": sha256_text(candidate_text),
            "candidate_web_check_count": 1,
            "candidate_web_source_ids": ["WEB-1"],
            "excluded_candidate_web_check_artifact": (
                excluded_path.relative_to(tmp_path).as_posix()
            ),
            "excluded_candidate_web_check_sha256": sha256_text(excluded_text),
            "excluded_candidate_web_check_count": 1,
            "excluded_candidate_web_source_ids": ["WEB-X"],
            "candidate_web_check_summary": {
                "source_count": 1,
                "excluded_source_count": 1,
                "subject_count": 1,
                "final_candidate_subject_count": 1,
                "candidate_expansion_subject_count": 0,
                "expansion_paths": [],
                "verification_focus": ["listed_security_and_exact_ticker"],
            },
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_row = {
        "schema_version": "bad.candidate_web_check",
        "run_id": "RUN-other",
        "candidate_rank": "1",
        "candidate_company_name": "",
        "verification_focus": [],
        "source_id": "WEB-1",
        "source_url": "https://example.test/source-a",
        "url": "https://example.test/source-b",
        "query": "",
        "opened_text": "raw copied text",
        "body": "raw copied body text",
    }
    bad_candidate_text = canonical_json(bad_row) + "\n"
    candidate_path.write_text(bad_candidate_text, encoding="utf-8")
    bad_excluded_row = {
        **excluded_row,
        "schema_version": "bad.excluded_candidate_web_check",
        "run_id": "RUN-other",
        "source_id": "WEB-Y",
    }
    bad_excluded_text = canonical_json(bad_excluded_row) + "\n"
    excluded_path.write_text(bad_excluded_text, encoding="utf-8")
    manifest = read_json(manifest_path)
    manifest["candidate_web_check_sha256"] = "0" * 64
    manifest["candidate_web_check_count"] = 2
    manifest["candidate_web_source_ids"] = ["WEB-OTHER"]
    manifest["excluded_candidate_web_check_sha256"] = "1" * 64
    manifest["excluded_candidate_web_check_count"] = 2
    manifest["excluded_candidate_web_source_ids"] = ["WEB-X"]
    manifest["candidate_web_check_summary"] = {
        "source_count": 2,
        "excluded_source_count": 2,
        "subject_count": 1,
        "final_candidate_subject_count": 2,
        "candidate_expansion_subject_count": 1,
        "expansion_paths": ["CONTINUATION"],
        "verification_focus": ["listed_security_and_exact_ticker"],
    }
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: context manifest candidate_web_check_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 "
        "schema_version mismatch"
    ) in findings
    assert "2030-01-10.json: context manifest candidate_web_check:1 run_id mismatch" in findings
    assert "2030-01-10.json: context manifest candidate_web_check count mismatch" in findings
    assert "2030-01-10.json: context manifest candidate_web_check source_ids mismatch" in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 "
        "required_fields missing"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 "
        "candidate_company_name invalid"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 "
        "candidate_path_type invalid"
    ) in findings
    assert "2030-01-10.json: context manifest candidate_web_check:1 query invalid" in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 "
        "candidate_rank invalid"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 source_url mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 opened_text present"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 body/content present"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_web_check:1 "
        "verification_focus invalid"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check subject_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_web_check "
        "final_candidate_subject_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check "
        "candidate_expansion_subject_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check "
        "expansion_paths mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest excluded_candidate_web_check_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest excluded_candidate_web_check:1 "
        "schema_version mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest excluded_candidate_web_check:1 "
        "run_id mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest excluded_candidate_web_check count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest excluded_candidate_web_check "
        "source_ids mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check source_count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_web_check "
        "excluded_source_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_web_check "
        "verification_focus mismatch"
    ) in findings


def test_provenance_audit_validates_candidate_verification_artifact(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    artifact_dir = (
        tmp_path / "runs" / "checkpoints" / "candidate_verifications" / "RUN-linked"
    )
    artifact_dir.mkdir(parents=True)
    artifact_path = artifact_dir / "candidate_verification.json"
    required_dimensions = [
        "listed_security_and_exact_ticker",
        "recent_disclosures_and_news",
    ]
    payload = {
        "schema_version": "nslab.candidate_verification.v1",
        "run_id": "RUN-linked",
        "required_dimensions": required_dimensions,
        "subject_count": 2,
        "findings": [
            {
                "subject_type": "final_candidate",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "query": "candidate verification",
                "source_count": 1,
                "excluded_source_count": 0,
                "accepted_source_ids": ["WEB-1"],
                "excluded_source_ids": [],
                "verification_dimensions": [
                    {
                        "name": "listed_security_and_exact_ticker",
                        "status": "source_collected",
                        "evidence_source_ids": ["WEB-1"],
                    },
                    {
                        "name": "recent_disclosures_and_news",
                        "status": "needs_company_discovery",
                        "evidence_source_ids": [],
                    },
                ],
                "d_minus_one_market_data_only": False,
                "uncertainties": [],
            },
            {
                "subject_type": "candidate_expansion",
                "candidate_rank": 0,
                "candidate_ticker": "",
                "candidate_company_name": "ExpansionCo",
                "candidate_path_type": "CONTINUATION",
                "query": "candidate verification expansion",
                "source_count": 0,
                "excluded_source_count": 1,
                "accepted_source_ids": [],
                "excluded_source_ids": ["WEB-X"],
                "verification_dimensions": [
                    {
                        "name": "listed_security_and_exact_ticker",
                        "status": "no_cutoff_safe_source",
                        "evidence_source_ids": [],
                    },
                    {
                        "name": "recent_disclosures_and_news",
                        "status": "no_cutoff_safe_source",
                        "evidence_source_ids": [],
                    },
                ],
                "d_minus_one_market_data_only": True,
                "uncertainties": [],
            },
        ],
    }
    artifact_text = canonical_json(payload)
    artifact_path.write_text(artifact_text, encoding="utf-8")
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "candidate_web_check_count": 1,
            "candidate_web_source_ids": ["WEB-1"],
            "excluded_candidate_web_check_count": 1,
            "excluded_candidate_web_source_ids": ["WEB-X"],
            "candidate_verification_artifact": artifact_path.relative_to(
                tmp_path
            ).as_posix(),
            "candidate_verification_sha256": sha256_text(artifact_text),
            "candidate_verification_count": 2,
            "candidate_verification_summary": {
                "required_dimensions": required_dimensions,
                "finding_count": 2,
                "subject_count": 2,
                "status_counts": {
                    "source_collected": 1,
                    "needs_company_discovery": 1,
                    "no_cutoff_safe_source": 2,
                },
                "subjects_without_cutoff_safe_sources": 1,
                "candidate_expansion_subject_count": 1,
                "d_minus_one_only_subject_count": 1,
            },
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_payload = {
        **payload,
        "schema_version": "bad.candidate_verification",
        "run_id": "RUN-other",
        "required_dimensions": ["listed_security_and_exact_ticker"],
        "subject_count": 1,
        "findings": [
            {
                **payload["findings"][0],
                "source_count": 2,
                "excluded_source_count": 1,
                "accepted_source_ids": ["WEB-Z"],
                "excluded_source_ids": ["WEB-Y"],
                "verification_dimensions": [
                    {
                        "name": "wrong_dimension",
                        "status": "unexpected_status",
                        "evidence_source_ids": [],
                    }
                ],
                "d_minus_one_market_data_only": True,
            }
        ],
    }
    bad_artifact_text = canonical_json(bad_payload)
    artifact_path.write_text(bad_artifact_text, encoding="utf-8")
    manifest = read_json(manifest_path)
    manifest["candidate_verification_sha256"] = "0" * 64
    manifest["candidate_verification_count"] = 2
    manifest["candidate_verification_summary"] = {
        "required_dimensions": required_dimensions,
        "finding_count": 2,
        "subject_count": 2,
        "status_counts": {"source_collected": 1},
        "subjects_without_cutoff_safe_sources": 1,
        "candidate_expansion_subject_count": 1,
        "d_minus_one_only_subject_count": 0,
    }
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: context manifest candidate_verification_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "schema_version mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification run_id mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "required_dimensions mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification count mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "subject_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "dimension_coverage mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "status_counts mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "source_counts mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "accepted_source_ids mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "excluded_source_ids mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification:1 "
        "accepted_source_ids mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification:1 "
        "excluded_source_ids mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "candidate_expansion_subject_count mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "subjects_without_cutoff_safe_sources mismatch"
    ) in findings
    assert (
        "2030-01-10.json: context manifest candidate_verification "
        "d_minus_one_only_subject_count mismatch"
    ) in findings


def test_provenance_audit_requires_report_sections(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    write_json(run_prediction_path, prediction)
    run_report_path.write_text(
        _preopen_report_text(omit_heading=PREOPEN_REPORT_SECTION_HEADINGS[-1]),
        encoding="utf-8",
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
            "report_artifact": run_report_path.relative_to(tmp_path).as_posix(),
            "report_sha256": sha256_text(run_report_path.read_text(encoding="utf-8")),
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: context manifest report_artifact missing required "
        "sections: ## 13. Memory Coverage"
    ) in result["findings"]


def test_provenance_audit_rejects_empty_report_sections(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    write_json(run_prediction_path, prediction)
    run_report_path.write_text(
        _preopen_report_text(empty_heading=PREOPEN_REPORT_SECTION_HEADINGS[3]),
        encoding="utf-8",
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
            "report_artifact": run_report_path.relative_to(tmp_path).as_posix(),
            "report_sha256": sha256_text(run_report_path.read_text(encoding="utf-8")),
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: context manifest report_artifact empty required "
        "sections: ## 4. Dominant Sector Hypotheses"
    ) in result["findings"]


def test_provenance_audit_validates_final_synthesis_context_artifact(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = {
        "blind_artifact_sha256": "abc123",
        "context_manifest_id": "RUN-linked",
        "blind_analysis": _blind_analysis_with_provenance(),
        "dominant_sectors": [_sector_with_provenance()],
        "candidates": [_candidate_with_provenance()],
    }
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        _preopen_report_text(), encoding="utf-8"
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    write_json(run_prediction_path, prediction)
    run_report_path.write_text(_preopen_report_text(), encoding="utf-8")
    final_context_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-linked"
        / "final_synthesis_context.json"
    )
    write_json(
        final_context_path,
        {
            "schema_version": "nslab.final_synthesis_context.v1",
            "run_id": "RUN-other",
            "prompt_version": "synthesis.final.v1",
            "required_inputs": ["wrong_input"],
            "payload_sha256": "bad-payload-hash",
            "input_summary": {"current_news_count": 99},
            "payload": {
                "required_inputs": ["current_news"],
                "current_news": ["pre-cutoff news"],
            },
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
            "report_artifact": run_report_path.relative_to(tmp_path).as_posix(),
            "report_sha256": sha256_text(run_report_path.read_text(encoding="utf-8")),
            "final_synthesis_context_artifact": final_context_path.relative_to(
                tmp_path
            ).as_posix(),
            "final_synthesis_context_sha256": "0" * 64,
            "final_synthesis_context_summary": {"current_news_count": 1},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert (
        "2030-01-10.json: context manifest final_synthesis_context_sha256 mismatch"
        in findings
    )
    assert "2030-01-10.json: final_synthesis_context run_id mismatch" in findings
    assert (
        "2030-01-10.json: final_synthesis_context payload_sha256 mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: final_synthesis_context required_inputs mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: final_synthesis_context input_summary mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: context manifest final_synthesis_context_summary mismatch"
        in findings
    )


def test_provenance_audit_validates_final_synthesis_context_embedded_artifacts(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = _sealed_prediction_payload()
    write_json(tmp_path / "predictions" / "2030-01-10.json", prediction)
    report_text = _preopen_report_text()
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        report_text, encoding="utf-8"
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    write_json(run_prediction_path, prediction)
    run_report_path.write_text(report_text, encoding="utf-8")

    web_source = {
        "schema_version": "nslab.web_source.v1",
        "source_id": "WEB-1",
        "query": "cutoff safe source",
        "title": "Cutoff-safe source",
        "url": "https://example.test/source",
        "source_url": "https://example.test/source",
        "snippet": "Source snippet.",
        "published_at": "2030-01-10T08:30:00+09:00",
        "timestamp_precision": "datetime",
        "retrieved_at": "2030-01-10T08:40:00+09:00",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "time_verified": True,
        "available_before_cutoff": True,
        "content_sha256": "b" * 64,
        "opened_text_sha256": "d" * 64,
        "opened_text_excerpt": "web excerpt",
    }
    web_source_context = {
        "source_id": "WEB-1",
        "query": "cutoff safe source",
        "title": "Cutoff-safe source",
        "url": "https://example.test/source",
        "snippet": "Source snippet.",
        "published_at": "2030-01-10T08:30:00+09:00",
        "timestamp_precision": "datetime",
        "time_verified": True,
        "content_sha256": "b" * 64,
        "opened_text_excerpt": "web excerpt",
    }
    web_source_2 = {
        **web_source,
        "source_id": "WEB-2",
        "query": "second cutoff safe source",
        "title": "Second cutoff-safe source",
        "url": "https://example.test/source-2",
        "source_url": "https://example.test/source-2",
        "snippet": "Second source snippet.",
        "content_sha256": "e" * 64,
        "opened_text_sha256": "f" * 64,
        "opened_text_excerpt": "second web excerpt",
    }
    web_source_context_2 = {
        **web_source_context,
        "source_id": "WEB-2",
        "query": "second cutoff safe source",
        "title": "Second cutoff-safe source",
        "url": "https://example.test/source-2",
        "snippet": "Second source snippet.",
        "content_sha256": "e" * 64,
        "opened_text_excerpt": "second web excerpt",
    }
    web_source_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "web_sources"
        / "RUN-linked"
        / "web_sources.jsonl"
    )
    web_source_text = canonical_json(web_source_2) + "\n" + canonical_json(web_source) + "\n"
    web_source_path.parent.mkdir(parents=True)
    web_source_path.write_text(web_source_text, encoding="utf-8")

    event_cluster = {
        "schema_version": "nslab.news_event_cluster.v1",
        "run_id": "RUN-linked",
        "cluster_id": "EVCL-1",
        "cluster_index": 1,
        "cluster_method": "exact_test_v1",
        "row_numbers": [1],
        "event_ids": ["EVT-1"],
        "source_ids": ["SRC-1"],
        "row_count": 1,
        "exact_duplicate_count": 0,
    }
    event_cluster_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "event_clusters"
        / "RUN-linked"
        / "event_clusters.jsonl"
    )
    event_cluster_text = canonical_json(event_cluster) + "\n"
    event_cluster_path.parent.mkdir(parents=True)
    event_cluster_path.write_text(event_cluster_text, encoding="utf-8")

    semantic_plan = {
        "schema_version": "nslab.semantic_retrieval_plan.v1",
        "run_id": "RUN-linked",
        "prompt_sha256": "semantic-plan-hash",
        "required_categories": ["positive_analogs"],
        "queries": [
            {
                "category": "positive_analogs",
                "query": "positive structural analog",
                "rationale": "positive cases",
            }
        ],
    }
    semantic_row = {
        "schema_version": "nslab.semantic_retrieval_result.v1",
        "run_id": "RUN-linked",
        "query_index": 1,
        "category": "positive_analogs",
        "query": "positive structural analog",
        "query_sha256": sha256_text("positive structural analog"),
        "included_episode_ids": [],
        "excluded_episode_ids": [],
        "result_count": 0,
        "excluded_count": 0,
    }
    semantic_summary = {
        "required_categories": ["positive_analogs"],
        "category_query_counts": {"positive_analogs": 1},
        "query_count": 1,
        "included_episode_count": 0,
        "excluded_episode_count": 0,
        "retrieval_zero_is_valid": True,
    }
    semantic_dir = (
        tmp_path / "runs" / "checkpoints" / "semantic_retrieval" / "RUN-linked"
    )
    semantic_plan_path = semantic_dir / "semantic_retrieval_plan.json"
    semantic_path = semantic_dir / "semantic_retrieval.jsonl"
    semantic_dir.mkdir(parents=True)
    write_json(semantic_plan_path, semantic_plan)
    semantic_text = canonical_json(semantic_row) + "\n"
    semantic_path.write_text(semantic_text, encoding="utf-8")

    candidate_web_check = {
        "schema_version": "nslab.candidate_web_check.v1",
        "run_id": "RUN-linked",
        "candidate_rank": 1,
        "candidate_ticker": "UNKNOWN",
        "candidate_company_name": "CandidateCo",
        "candidate_path_type": "SINGLE_EVENT",
        "candidate_subject_type": "final_candidate",
        "verification_focus": ["listed_security_and_exact_ticker"],
        "source_id": "WEB-1",
        "source_url": "https://example.test/source",
        "url": "https://example.test/source",
        "query": "candidate verification",
        "title": "Candidate source",
        "snippet": "Candidate verification source.",
        "published_at": "2030-01-10T08:30:00+09:00",
        "timestamp_precision": "datetime",
        "retrieved_at": "2030-01-10T08:40:00+09:00",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "time_verified": True,
        "available_before_cutoff": True,
        "content_sha256": "c" * 64,
        "opened_text_excerpt": "excerpt",
    }
    candidate_web_context = {
        "candidate_rank": 1,
        "candidate_ticker": "UNKNOWN",
        "candidate_company_name": "CandidateCo",
        "candidate_path_type": "SINGLE_EVENT",
        "candidate_subject_type": "final_candidate",
        "candidate_expansion_path": None,
        "candidate_expansion_hypothesis": None,
        "candidate_investigation_questions": None,
        "verification_focus": ["listed_security_and_exact_ticker"],
        "source_id": "WEB-1",
        "query": "candidate verification",
        "title": "Candidate source",
        "url": "https://example.test/source",
        "snippet": "Candidate verification source.",
        "published_at": "2030-01-10T08:30:00+09:00",
        "timestamp_precision": "datetime",
        "time_verified": True,
        "content_sha256": "c" * 64,
        "opened_text_excerpt": "excerpt",
    }
    candidate_web_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_web_checks"
        / "RUN-linked"
        / "candidate_web_checks.jsonl"
    )
    candidate_web_text = canonical_json(candidate_web_check) + "\n"
    candidate_web_path.parent.mkdir(parents=True)
    candidate_web_path.write_text(candidate_web_text, encoding="utf-8")

    news_novelty_review = {
        "schema_version": "nslab.news_novelty_review.v1",
        "run_id": "RUN-linked",
        "prompt_sha256": "novelty-hash",
        "cluster_count": 1,
        "reviewed_cluster_count": 1,
        "findings": [
            {
                "cluster_id": "EVCL-1",
                "cluster_index": 1,
                "novelty": "new",
                "time_verified": True,
            }
        ],
        "excluded_after_cutoff_source_ids": [],
    }
    novelty_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "news_novelty_reviews"
        / "RUN-linked"
        / "news_novelty_review.json"
    )
    novelty_text = canonical_json(news_novelty_review)
    novelty_path.parent.mkdir(parents=True)
    novelty_path.write_text(novelty_text, encoding="utf-8")

    candidate_expansion = {
        "schema_version": "nslab.candidate_expansion.v1",
        "run_id": "RUN-linked",
        "prompt_sha256": "candidate-expansion-hash",
        "required_paths": ["SINGLE_EVENT"],
        "findings": [
            {
                "path": "SINGLE_EVENT",
                "hypothesis": "Direct catalyst route.",
                "candidate_names": ["DirectCandidate"],
                "sector_hypotheses": ["direct sector"],
                "investigation_questions": ["verify directness"],
                "evidence_source_ids": ["SRC-1"],
                "related_cluster_ids": ["EVCL-1"],
                "memory_episode_ids": [],
                "requires_web_company_discovery": True,
                "d_minus_one_market_data_only": False,
                "uncertainties": ["needs web check"],
            }
        ],
    }
    expansion_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_expansion"
        / "RUN-linked"
        / "candidate_expansion.json"
    )
    expansion_text = canonical_json(candidate_expansion)
    expansion_path.parent.mkdir(parents=True)
    expansion_path.write_text(expansion_text, encoding="utf-8")

    candidate_verification = {
        "schema_version": "nslab.candidate_verification.v1",
        "run_id": "RUN-linked",
        "required_dimensions": ["listed_security_and_exact_ticker"],
        "subject_count": 1,
        "findings": [
            {
                "subject_type": "final_candidate",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "query": "candidate verification",
                "source_count": 1,
                "excluded_source_count": 0,
                "accepted_source_ids": ["WEB-1"],
                "excluded_source_ids": [],
                "verification_dimensions": [
                    {
                        "name": "listed_security_and_exact_ticker",
                        "status": "source_collected",
                        "evidence_source_ids": ["WEB-1"],
                    }
                ],
                "d_minus_one_market_data_only": False,
                "uncertainties": [],
            }
        ],
    }
    verification_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_verifications"
        / "RUN-linked"
        / "candidate_verification.json"
    )
    verification_text = canonical_json(candidate_verification)
    verification_path.parent.mkdir(parents=True)
    verification_path.write_text(verification_text, encoding="utf-8")

    red_team = {
        "schema_version": "nslab.red_team_artifact.v1",
        "run_id": "RUN-linked",
        "source_prediction_id": "PRED-sealed",
        "prompt_version": "red_team.candidate_attack.v2",
        "prompt_sha256": "red-team-hash",
        "created_at": "2030-01-10T08:59:59+09:00",
        "candidate_count": 1,
        "required_attack_checks": ["novelty_not_recycled"],
        "candidate_findings": [
            {
                "candidate_rank": 1,
                "passed_to_synthesis": True,
                "attack_checks": [
                    {
                        "name": "novelty_not_recycled",
                        "status": "needs_synthesis_review",
                        "passed_to_synthesis": True,
                    }
                ],
            }
        ],
    }
    red_team_path = tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json"
    red_team_path.parent.mkdir(parents=True)
    red_team_path.write_text(canonical_json(red_team), encoding="utf-8")

    final_payload = {
        "required_inputs": list(FINAL_SYNTHESIS_REQUIRED_INPUTS),
        "current_news": ["pre-cutoff news"],
        "web_research": {
            "queries": ["second cutoff safe source", "cutoff safe source"],
            "included_sources": ["WEB-2", "WEB-1"],
            "sources": [web_source_context_2, web_source_context],
            "excluded_after_cutoff_source_ids": [],
        },
        "event_clusters": [event_cluster],
        "additional_semantic_retrieval": {
            "plan_artifact": semantic_plan_path.relative_to(tmp_path).as_posix(),
            "artifact": semantic_path.relative_to(tmp_path).as_posix(),
            "summary": semantic_summary,
            "rows": [semantic_row],
            "episodes": [],
            "excluded_episode_ids": [],
        },
        "candidate_research": {"candidates": prediction["candidates"]},
        "news_novelty_review": news_novelty_review,
        "open_world_candidate_expansion": candidate_expansion,
        "candidate_web_checks": [candidate_web_context],
        "candidate_verification": candidate_verification,
        "red_team_output": red_team,
    }
    final_context_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-linked"
        / "final_synthesis_context.json"
    )

    def write_final_context(payload: dict[str, object]) -> str:
        artifact = {
            "schema_version": "nslab.final_synthesis_context.v1",
            "run_id": "RUN-linked",
            "prompt_version": "synthesis.final.v1",
            "required_inputs": payload["required_inputs"],
            "payload_sha256": sha256_text(canonical_json(payload)),
            "input_summary": final_synthesis_input_summary(payload),
            "payload": payload,
        }
        artifact_text = canonical_json(artifact)
        final_context_path.parent.mkdir(parents=True, exist_ok=True)
        final_context_path.write_text(artifact_text, encoding="utf-8")
        return artifact_text

    final_context_text = write_final_context(final_payload)
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": prediction["blind_artifact_sha256"],
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "semantic_retrieval_plan": "semantic-plan-hash",
                "news_novelty_review": "novelty-hash",
                "candidate_expansion": "candidate-expansion-hash",
                "red_team_candidate_review": "red-team-hash",
                "final_synthesis": "final-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
            "report_artifact": run_report_path.relative_to(tmp_path).as_posix(),
            "report_sha256": sha256_text(run_report_path.read_text(encoding="utf-8")),
            "web_queries": ["second cutoff safe source", "cutoff safe source"],
            "web_sources": ["WEB-1", "WEB-2"],
            "excluded_web_source_ids": [],
            "web_source_artifact": web_source_path.relative_to(tmp_path).as_posix(),
            "web_source_sha256": sha256_text(web_source_text),
            "event_cluster_artifact": event_cluster_path.relative_to(
                tmp_path
            ).as_posix(),
            "event_cluster_sha256": sha256_text(event_cluster_text),
            "event_cluster_count": 1,
            "event_cluster_summary": {
                "cluster_count": 1,
                "source_row_count": 1,
                "exact_duplicate_count": 0,
                "exact_duplicate_cluster_count": 0,
                "cluster_method": "exact_test_v1",
            },
            "semantic_retrieval_plan_artifact": semantic_plan_path.relative_to(
                tmp_path
            ).as_posix(),
            "semantic_retrieval_plan_sha256": sha256_text(
                semantic_plan_path.read_text(encoding="utf-8")
            ),
            "semantic_retrieval_artifact": semantic_path.relative_to(
                tmp_path
            ).as_posix(),
            "semantic_retrieval_sha256": sha256_text(semantic_text),
            "semantic_retrieval_query_count": 1,
            "semantic_retrieval_episode_ids": [],
            "excluded_semantic_retrieval_episode_ids": [],
            "semantic_retrieval_summary": semantic_summary,
            "candidate_web_check_artifact": candidate_web_path.relative_to(
                tmp_path
            ).as_posix(),
            "candidate_web_check_sha256": sha256_text(candidate_web_text),
            "candidate_web_check_count": 1,
            "candidate_web_source_ids": ["WEB-1"],
            "excluded_candidate_web_check_count": 0,
            "excluded_candidate_web_source_ids": [],
            "candidate_web_check_summary": {
                "source_count": 1,
                "excluded_source_count": 0,
                "subject_count": 1,
                "final_candidate_subject_count": 1,
                "candidate_expansion_subject_count": 0,
                "expansion_paths": [],
                "verification_focus": ["listed_security_and_exact_ticker"],
            },
            "news_novelty_review_artifact": novelty_path.relative_to(
                tmp_path
            ).as_posix(),
            "news_novelty_review_sha256": sha256_text(novelty_text),
            "news_novelty_review_count": 1,
            "news_novelty_review_summary": {
                "cluster_count": 1,
                "reviewed_cluster_count": 1,
                "novelty_counts": {"new": 1},
                "time_verified_count": 1,
                "excluded_after_cutoff_source_count": 0,
            },
            "candidate_expansion_artifact": expansion_path.relative_to(
                tmp_path
            ).as_posix(),
            "candidate_expansion_sha256": sha256_text(expansion_text),
            "candidate_expansion_count": 1,
            "candidate_expansion_summary": {
                "required_paths": ["SINGLE_EVENT"],
                "path_counts": {"SINGLE_EVENT": 1},
                "finding_count": 1,
                "candidate_name_count": 1,
                "requires_web_company_discovery_count": 1,
                "continuation_d_minus_one_only_verified": False,
            },
            "candidate_verification_artifact": verification_path.relative_to(
                tmp_path
            ).as_posix(),
            "candidate_verification_sha256": sha256_text(verification_text),
            "candidate_verification_count": 1,
            "candidate_verification_summary": {
                "required_dimensions": ["listed_security_and_exact_ticker"],
                "finding_count": 1,
                "subject_count": 1,
                "status_counts": {"source_collected": 1},
                "subjects_without_cutoff_safe_sources": 0,
                "candidate_expansion_subject_count": 0,
                "d_minus_one_only_subject_count": 0,
            },
            "red_team_artifacts": [red_team_path.relative_to(tmp_path).as_posix()],
            "red_team_summary": {
                "candidate_count": 1,
                "required_attack_checks": ["novelty_not_recycled"],
                "required_attack_check_count": 1,
                "finding_count": 1,
                "all_findings_passed_to_synthesis": True,
            },
            "final_synthesis_context_artifact": final_context_path.relative_to(
                tmp_path
            ).as_posix(),
            "final_synthesis_context_sha256": sha256_text(final_context_text),
            "final_synthesis_context_summary": final_synthesis_input_summary(
                final_payload
            ),
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    bad_payload = json.loads(canonical_json(final_payload))
    bad_payload["web_research"] = {
        **bad_payload["web_research"],
        "sources": [],
    }
    bad_payload["event_clusters"] = []
    bad_payload["additional_semantic_retrieval"] = {
        **bad_payload["additional_semantic_retrieval"],
        "rows": [],
    }
    bad_payload["news_novelty_review"] = {
        **news_novelty_review,
        "findings": [],
    }
    bad_payload["open_world_candidate_expansion"] = {
        **candidate_expansion,
        "findings": [],
    }
    bad_payload["candidate_web_checks"] = []
    bad_payload["candidate_verification"] = {
        **candidate_verification,
        "findings": [],
    }
    bad_payload["red_team_output"] = {
        **red_team,
        "candidate_findings": [],
    }
    bad_context_text = write_final_context(bad_payload)
    manifest = read_json(manifest_path)
    manifest["final_synthesis_context_sha256"] = sha256_text(bad_context_text)
    manifest["final_synthesis_context_summary"] = final_synthesis_input_summary(
        bad_payload
    )
    write_json(manifest_path, manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: final_synthesis_context web_research mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: final_synthesis_context event_clusters mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: final_synthesis_context "
        "additional_semantic_retrieval mismatch"
    ) in findings
    assert (
        "2030-01-10.json: final_synthesis_context candidate_web_checks mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: final_synthesis_context news_novelty_review mismatch"
        in findings
    )
    assert (
        "2030-01-10.json: final_synthesis_context "
        "open_world_candidate_expansion mismatch"
    ) in findings
    assert (
        "2030-01-10.json: final_synthesis_context "
        "candidate_verification mismatch"
    ) in findings
    assert (
        "2030-01-10.json: final_synthesis_context red_team_output mismatch"
        in findings
    )


def test_final_synthesis_manifest_counts_allow_capped_current_news() -> None:
    summary = {
        "current_news_count": 12,
        "event_cluster_count": 1180,
        "retrieved_raw_episode_count": 0,
        "counterexample_count": 0,
        "web_source_count": 0,
        "global_brain_file_count": 0,
        "shard_brain_file_count": 0,
    }
    mismatches = _final_synthesis_manifest_count_mismatches(
        {"included_news_row_count": 1182, "event_cluster_count": 1180},
        summary,
    )

    assert mismatches == {}

    failed_summary = summary | {"event_cluster_count": 1179}
    failed = _final_synthesis_manifest_count_mismatches(
        {"included_news_row_count": 1182, "event_cluster_count": 1180},
        failed_summary,
    )

    assert failed == {"event_cluster_count": {"expected": 1180, "observed": 1179}}


def test_provenance_audit_verifies_context_brain_file_hashes(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    brain_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "brain_context"
        / "RUN-linked"
        / "brain"
        / "00_world_model.md"
    )
    shard_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "brain_context"
        / "RUN-linked"
        / "shards"
        / "shard_0001.md"
    )
    brain_path.parent.mkdir(parents=True)
    shard_path.parent.mkdir(parents=True)
    brain_path.write_text("world model context", encoding="utf-8")
    shard_path.write_text("shard context", encoding="utf-8")
    brain_ref = brain_path.relative_to(tmp_path).as_posix()
    shard_ref = shard_path.relative_to(tmp_path).as_posix()
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "dominant_sectors": [_sector_with_provenance()],
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_files": [brain_ref],
            "brain_file_hashes": {brain_ref: file_sha256(brain_path)},
            "shard_brain_files": [shard_ref],
            "shard_brain_file_hashes": {shard_ref: file_sha256(shard_path)},
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    manifest = read_json(tmp_path / "runs" / "manifests" / "RUN-linked.json")
    manifest["brain_file_hashes"][brain_ref] = "0" * 64
    manifest["shard_brain_file_hashes"]["runs/checkpoints/brain_context/RUN-linked/shards/extra.md"] = (
        "1" * 64
    )
    write_json(tmp_path / "runs" / "manifests" / "RUN-linked.json", manifest)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    assert (
        "2030-01-10.json: context manifest brain file sha256 mismatch: "
        f"{brain_ref}"
    ) in failed["findings"]
    assert any(
        finding.startswith(
            "2030-01-10.json: context manifest unlisted shard_brain_file_hashes:"
        )
        for finding in failed["findings"]
    )


def test_provenance_audit_verifies_memory_sweep_artifacts(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    news_path = tmp_path / "data" / "raw" / "news" / "sweep_news.csv"
    news_path.parent.mkdir(parents=True)
    news_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-08","08:00:00","SweepCo event","Source fixture."\n',
        encoding="utf-8",
    )
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True)
    for episode_id, summary in (
        ("EP-sweep-1", "Sweep source one."),
        ("EP-sweep-2", "Sweep source two."),
    ):
        episode = ResearchEpisode(
            episode_id=episode_id,
            trade_date=date(2030, 1, 8),
            cutoff_at=datetime(2030, 1, 8, 8, 59, 59, tzinfo=KST),
            created_at=datetime(2030, 1, 8, 16, 0, 0, tzinfo=KST),
            research_version="memory-sweep-provenance-test-v1",
            input_news_files=[news_path.relative_to(tmp_path).as_posix()],
            input_news_hashes=[file_sha256(news_path)],
            price_source_snapshot={"source": "provenance-test"},
            blind_analysis=BlindAnalysis(
                summary=summary,
                open_world_mechanisms=["sweep source hash -> reproducible context"],
                provenance=_provenance("sweep_blind_analysis"),
            ),
            provenance=_provenance("sweep_episode"),
            available_from=datetime(2030, 1, 9, 0, 0, 0, tzinfo=KST),
        )
        write_json(accepted_dir / f"{episode_id}.json", episode.model_dump(mode="json"))
    record = _brain_record_for_sweep_audit(
        "REC-sweep-1",
        episode_id="NSLAB-20300109-RECORD-SWEEP",
        available_from=datetime(2030, 1, 9, 0, 0, 0, tzinfo=KST),
    )
    future_record = _brain_record_for_sweep_audit(
        "REC-sweep-future",
        episode_id="NSLAB-20300109-RECORD-SWEEP",
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    _store_brain_records_for_sweep_audit(tmp_path, [record, future_record])
    sweep_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "memory_sweep"
        / "RUN-linked"
        / "shard_0001.json"
    )
    sweep_ref = sweep_path.relative_to(tmp_path).as_posix()
    source_hashes = {
        "EP-sweep-1": file_sha256(accepted_dir / "EP-sweep-1.json"),
        "EP-sweep-2": file_sha256(accepted_dir / "EP-sweep-2.json"),
    }
    sweep_payload = {
        "schema_version": "nslab.memory_sweep_contribution.v1",
        "cache_key": "SWEEP-linked",
        "mode": "exhaustive",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "brain_version": "brain-linked",
        "episode_shard_sha256": _sweep_shard_hash(source_hashes),
        "episode_shard_source_hashes": source_hashes,
        "episode_count": 2,
        "episode_ids": ["EP-sweep-1", "EP-sweep-2"],
        "from_cache": False,
    }
    write_json(sweep_path, sweep_payload)
    record_sweep_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "record_sweep"
        / "RUN-linked"
        / "record_shard_0001.json"
    )
    record_sweep_ref = record_sweep_path.relative_to(tmp_path).as_posix()
    record_source_hashes = {
        "REC-sweep-1": record.normalized_payload_sha256,
    }
    record_sweep_payload = {
        "schema_version": "nslab.record_memory_sweep_contribution.v1",
        "cache_key": "RECSWEEP-linked",
        "mode": "exhaustive",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "brain_version": "brain-linked",
        "record_shard_sha256": _record_sweep_shard_hash(record_source_hashes),
        "record_shard_source_hashes": record_source_hashes,
        "record_count": 1,
        "record_ids": ["REC-sweep-1"],
        "from_cache": False,
    }
    write_json(record_sweep_path, record_sweep_payload)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "dominant_sectors": [_sector_with_provenance()],
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "brain_version": "brain-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "swept_episode_ids": ["EP-sweep-1", "EP-sweep-2"],
            "memory_sweep_artifacts": [sweep_ref],
            "memory_sweep_artifact_hashes": {sweep_ref: file_sha256(sweep_path)},
            "memory_sweep_shard_count": 1,
            "memory_sweep_cache_hits": 0,
            "swept_record_ids": ["REC-sweep-1"],
            "swept_record_count": 1,
            "record_sweep_artifacts": [record_sweep_ref],
            "record_sweep_artifact_hashes": {
                record_sweep_ref: file_sha256(record_sweep_path),
            },
            "record_sweep_shard_count": 1,
            "record_sweep_cache_hits": 0,
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    tampered_sweep = {
        **sweep_payload,
        "schema_version": "tampered.memory_sweep",
        "episode_ids": ["EP-sweep-1"],
    }
    write_json(sweep_path, tampered_sweep)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    findings = failed["findings"]
    assert (
        "2030-01-10.json: context manifest memory sweep artifact sha256 mismatch: "
        f"{sweep_ref}"
    ) in findings
    assert (
        "2030-01-10.json: memory sweep artifact schema mismatch: "
        f"{sweep_ref}"
    ) in findings
    assert (
        "2030-01-10.json: memory sweep artifact episode_count mismatch: "
        f"{sweep_ref}"
    ) in findings
    assert (
        "2030-01-10.json: memory sweep artifact source hashes invalid: "
        f"{sweep_ref}"
    ) in findings
    assert (
        "2030-01-10.json: context manifest memory_sweep swept episode ids mismatch"
    ) in findings

    tampered_sweep = {
        **sweep_payload,
        "episode_shard_sha256": "0" * 64,
        "episode_shard_source_hashes": {
            **source_hashes,
            "EP-sweep-2": "1" * 64,
        },
    }
    write_json(sweep_path, tampered_sweep)

    failed_hashes = audit_provenance(tmp_path)

    assert not failed_hashes["passed"]
    hash_findings = failed_hashes["findings"]
    assert (
        "2030-01-10.json: memory sweep artifact episode_shard_sha256 mismatch: "
        f"{sweep_ref}"
    ) in hash_findings
    assert (
        "2030-01-10.json: memory sweep artifact source hash mismatch: "
        f"{sweep_ref}#EP-sweep-2"
    ) in hash_findings

    write_json(sweep_path, sweep_payload)
    tampered_record_sweep = {
        **record_sweep_payload,
        "schema_version": "tampered.record_sweep",
        "record_ids": [],
    }
    write_json(record_sweep_path, tampered_record_sweep)

    failed_record = audit_provenance(tmp_path)

    assert not failed_record["passed"]
    record_findings = failed_record["findings"]
    assert (
        "2030-01-10.json: context manifest record sweep artifact sha256 mismatch: "
        f"{record_sweep_ref}"
    ) in record_findings
    assert (
        "2030-01-10.json: record sweep artifact schema mismatch: "
        f"{record_sweep_ref}"
    ) in record_findings
    assert (
        "2030-01-10.json: record sweep artifact record_count mismatch: "
        f"{record_sweep_ref}"
    ) in record_findings
    assert (
        "2030-01-10.json: record sweep artifact source hashes invalid: "
        f"{record_sweep_ref}"
    ) in record_findings
    assert (
        "2030-01-10.json: context manifest record_sweep swept record ids mismatch"
    ) in record_findings

    tampered_record_sweep = {
        **record_sweep_payload,
        "record_shard_sha256": "0" * 64,
        "record_shard_source_hashes": {
            **record_source_hashes,
            "REC-sweep-1": "1" * 64,
        },
    }
    write_json(record_sweep_path, tampered_record_sweep)

    failed_record_hashes = audit_provenance(tmp_path)

    assert not failed_record_hashes["passed"]
    record_hash_findings = failed_record_hashes["findings"]
    assert (
        "2030-01-10.json: record sweep artifact record_shard_sha256 mismatch: "
        f"{record_sweep_ref}"
    ) in record_hash_findings
    assert (
        "2030-01-10.json: record sweep artifact source hash mismatch: "
        f"{record_sweep_ref}#REC-sweep-1"
    ) in record_hash_findings

    future_record_source_hashes = {
        "REC-sweep-future": future_record.normalized_payload_sha256,
    }
    future_record_sweep = {
        **record_sweep_payload,
        "record_shard_sha256": _record_sweep_shard_hash(future_record_source_hashes),
        "record_shard_source_hashes": future_record_source_hashes,
        "record_ids": ["REC-sweep-future"],
    }
    write_json(record_sweep_path, future_record_sweep)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "brain_version": "brain-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "swept_episode_ids": ["EP-sweep-1", "EP-sweep-2"],
            "memory_sweep_artifacts": [sweep_ref],
            "memory_sweep_artifact_hashes": {sweep_ref: file_sha256(sweep_path)},
            "memory_sweep_shard_count": 1,
            "memory_sweep_cache_hits": 0,
            "swept_record_ids": ["REC-sweep-future"],
            "swept_record_count": 1,
            "record_sweep_artifacts": [record_sweep_ref],
            "record_sweep_artifact_hashes": {
                record_sweep_ref: file_sha256(record_sweep_path),
            },
            "record_sweep_shard_count": 1,
            "record_sweep_cache_hits": 0,
        },
    )

    failed_future_record = audit_provenance(tmp_path)

    assert not failed_future_record["passed"]
    assert (
        "2030-01-10.json: record sweep artifact exposes future record: "
        f"{record_sweep_ref}#REC-sweep-future"
    ) in failed_future_record["findings"]


def test_provenance_audit_verifies_sealed_blind_prediction_hash(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = _sealed_prediction_payload()
    prediction_path = tmp_path / "predictions" / "2030-01-10.json"
    manifest_path = tmp_path / "runs" / "manifests" / "RUN-linked.json"
    write_json(prediction_path, prediction)
    write_json(
        manifest_path,
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": prediction["blind_artifact_sha256"],
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    tampered = read_json(prediction_path)
    tampered["blind_analysis"]["summary"] = "Changed after seal."
    write_json(prediction_path, tampered)

    tampered_result = audit_provenance(tmp_path)

    assert not tampered_result["passed"]
    assert "2030-01-10.json: blind_artifact_sha256 mismatch" in tampered_result["findings"]

    write_json(prediction_path, prediction)
    manifest = read_json(manifest_path)
    manifest["blind_artifact_sha256"] = "0" * 64
    write_json(manifest_path, manifest)

    manifest_mismatch = audit_provenance(tmp_path)

    assert not manifest_mismatch["passed"]
    assert (
        "2030-01-10.json: context manifest blind_artifact_sha256 mismatch"
    ) in manifest_mismatch["findings"]


def test_provenance_audit_verifies_manifest_prediction_artifact_blind_hash(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = _sealed_prediction_payload()
    prediction_path = tmp_path / "predictions" / "2030-01-10.json"
    write_json(prediction_path, prediction)
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    tampered_run_prediction = {
        **prediction,
        "blind_artifact_sha256": "0" * 64,
    }
    write_json(run_prediction_path, tampered_run_prediction)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": prediction["blind_artifact_sha256"],
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: context manifest prediction_artifact "
        "blind_artifact_sha256 mismatch"
    ) in result["findings"]
    assert (
        "2030-01-10.json: context manifest prediction_artifact "
        "manifest blind_artifact_sha256 mismatch"
    ) in result["findings"]


def test_provenance_audit_requires_manifest_prediction_artifact_schema_and_seal(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    prediction = _sealed_prediction_payload()
    prediction_path = tmp_path / "predictions" / "2030-01-10.json"
    write_json(prediction_path, prediction)
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    incomplete_run_prediction = {
        key: value
        for key, value in prediction.items()
        if key not in {"schema_version", "sealed_at"}
    }
    write_json(run_prediction_path, incomplete_run_prediction)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": prediction["blind_artifact_sha256"],
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "prediction_artifact": run_prediction_path.relative_to(tmp_path).as_posix(),
            "prediction_sha256": file_sha256(run_prediction_path),
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: context manifest prediction_artifact "
        "schema_version mismatch"
    ) in result["findings"]
    assert (
        "2030-01-10.json: context manifest prediction_artifact sealed_at missing"
    ) in result["findings"]


def test_provenance_audit_validates_semantic_import_source_segments(tmp_path: Path) -> None:
    raw_path = tmp_path / "data" / "raw" / "research" / "source.md"
    first_segment = "First sentence."
    second_segment = "Second sentence."
    raw_text = f"{first_segment}\n{second_segment}"
    raw_path.parent.mkdir(parents=True)
    raw_path.write_text(raw_text, encoding="utf-8")
    source_hash = file_sha256(raw_path)
    source_id = "SRC-semantic-source"
    source_segments = [
        {
            "index": 1,
            "char_start": 0,
            "char_end": len(first_segment),
            "text_sha256": sha256_text(first_segment),
            "excerpt": first_segment,
        },
        {
            "index": 2,
            "char_start": len(first_segment) + 1,
            "char_end": len(raw_text),
            "text_sha256": sha256_text(second_segment),
            "excerpt": second_segment,
        },
    ]
    provenance = {
        "source_id": source_id,
        "source_type": "semantic_llm_structured_import",
        "uri": raw_path.as_posix(),
        "content_sha256": source_hash,
    }
    prompt_sha256 = sha256_text(
        build_semantic_import_prompt(
            root=tmp_path,
            source_path=raw_path,
            source_sha256=source_hash,
            text=raw_text,
        )
    )
    output_text_provenance = [
        {
            "field_name": "blind_analysis.summary",
            "sentence_index": 1,
            "text_sha256": sha256_text("Imported from source."),
            "excerpt": "Imported from source.",
            "source_ids": [source_id],
            "source_segment_indices": [1, 2],
        },
        {
            "field_name": "blind_analysis.open_world_mechanisms",
            "item_index": 1,
            "sentence_index": 1,
            "text_sha256": sha256_text(
                "current evidence -> open-world blind mechanism"
            ),
            "excerpt": "current evidence -> open-world blind mechanism",
            "source_ids": [source_id],
            "source_segment_indices": [1, 2],
        },
    ]
    semantic_audit = {
        "prompt_version": "semantic_import.v1",
        "prompt_sha256": prompt_sha256,
        "source_path": raw_path.as_posix(),
        "source_sha256": source_hash,
        "source_text_sha256": sha256_text(raw_text),
        "source_segment_count": len(source_segments),
        "source_segments_sha256": sha256_text(canonical_json(source_segments)),
        "source_segments": source_segments,
        "output_text_provenance_count": len(output_text_provenance),
        "output_text_provenance_sha256": sha256_text(
            canonical_json(output_text_provenance)
        ),
        "output_text_provenance": output_text_provenance,
        "output_field_source_ids": {
            field_name: [source_id]
            for field_name in SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS
        },
    }
    episode = {
        "schema_version": "nslab.research_episode.v1",
        "episode_id": "EP-semantic",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "created_at": "2030-01-10T09:00:00+09:00",
        "research_version": "semantic-test",
        "input_news_files": [],
        "input_news_hashes": [],
        "input_audit": {"semantic_import": semantic_audit},
        "price_source_snapshot": {"source": "test"},
        "blind_analysis": {
            "summary": "Imported from source.",
            "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
            "initial_uncertainties": [],
            "provenance": [provenance],
        },
        "blind_predictions": [],
        "outcome_labels": {},
        "observed_events": [],
        "event_ticker_edges": [],
        "lessons": [],
        "counterexamples": [],
        "misses": [],
        "provenance": [provenance],
        "available_from": "2030-01-11T00:00:00+09:00",
    }
    episode_path = tmp_path / "research" / "accepted" / "EP-semantic.json"
    write_json(episode_path, episode)
    semantic_trace = _trace_payload(
        prompt_sha256=prompt_sha256,
        purpose="research_import.semantic",
        response_model="SemanticResearchDraft",
        prompt_version="semantic_import.v1",
        output={
            "schema_version": "nslab.semantic_research_draft.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "research_version": "semantic-test",
            "summary": "Imported from source.",
            "open_world_mechanisms": [
                "current evidence -> open-world blind mechanism"
            ],
            "initial_uncertainties": [],
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": {"source": "test"},
            "available_from": None,
        },
        checkpoint_id="LLMCKPT-semantic",
        trace_id="TRACE-semantic",
    )
    trace_path = tmp_path / "runs" / "traces" / "TRACE-semantic.json"
    write_json(trace_path, semantic_trace)
    _write_trace_checkpoint(tmp_path, semantic_trace)

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]
    assert result["checked_research_episode_files"] == 1

    missing_sentence = read_json(episode_path)
    reduced_output_text_provenance = output_text_provenance[:1]
    missing_sentence["input_audit"]["semantic_import"][
        "output_text_provenance"
    ] = reduced_output_text_provenance
    missing_sentence["input_audit"]["semantic_import"][
        "output_text_provenance_count"
    ] = len(reduced_output_text_provenance)
    missing_sentence["input_audit"]["semantic_import"][
        "output_text_provenance_sha256"
    ] = sha256_text(canonical_json(reduced_output_text_provenance))
    write_json(episode_path, missing_sentence)

    missing_sentence_result = audit_provenance(tmp_path)

    assert not missing_sentence_result["passed"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import "
        "output text provenance missing: blind_analysis.open_world_mechanisms"
    ) in missing_sentence_result["findings"]

    write_json(episode_path, episode)

    trace_path.unlink()
    missing_trace_result = audit_provenance(tmp_path)

    assert not missing_trace_result["passed"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import "
        "prompt hash has no matching trace"
    ) in missing_trace_result["findings"]

    write_json(trace_path, semantic_trace)
    _write_trace_checkpoint(tmp_path, semantic_trace)

    prompt_mismatch = read_json(episode_path)
    prompt_mismatch["input_audit"]["semantic_import"]["prompt_sha256"] = "0" * 64
    write_json(episode_path, prompt_mismatch)

    prompt_mismatch_result = audit_provenance(tmp_path)

    assert not prompt_mismatch_result["passed"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import prompt_sha256 mismatch"
    ) in prompt_mismatch_result["findings"]

    write_json(episode_path, episode)

    output_mismatch_trace = json.loads(json.dumps(semantic_trace))
    output_mismatch_trace["output"]["summary"] = "Altered after structured import."
    output_mismatch_trace["output_sha256"] = sha256_text(
        canonical_json(output_mismatch_trace["output"])
    )
    write_json(trace_path, output_mismatch_trace)
    _write_trace_checkpoint(tmp_path, output_mismatch_trace)

    output_mismatch_result = audit_provenance(tmp_path)

    assert not output_mismatch_result["passed"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import "
        "trace output blind_analysis.summary mismatch"
    ) in output_mismatch_result["findings"]

    write_json(trace_path, semantic_trace)
    _write_trace_checkpoint(tmp_path, semantic_trace)

    missing_field = read_json(episode_path)
    del missing_field["input_audit"]["semantic_import"]["output_field_source_ids"][
        "cutoff_at"
    ]
    write_json(episode_path, missing_field)

    missing_field_result = audit_provenance(tmp_path)

    assert not missing_field_result["passed"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import "
        "output_field_source_ids missing required fields: cutoff_at"
    ) in missing_field_result["findings"]

    write_json(episode_path, episode)

    tampered = read_json(episode_path)
    tampered["input_audit"]["semantic_import"]["source_segments"][0]["text_sha256"] = "0" * 64
    write_json(episode_path, tampered)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import source_segments_sha256 mismatch"
    ) in failed["findings"]
    assert (
        "research/accepted/EP-semantic.json: semantic_import segment 1 text_sha256 mismatch"
    ) in failed["findings"]


def test_provenance_audit_validates_research_episode_identity(tmp_path: Path) -> None:
    def episode_payload(
        episode_id: str,
        *,
        schema_version: str = "nslab.research_episode.v1",
        created_at: str = "2030-01-11T00:00:00+09:00",
        research_version: str = "identity-test-v1",
        price_source_snapshot: dict[str, object] | None = None,
        execution_protocol_version: object | None = None,
        blind_predictions: list[dict[str, object]] | None = None,
        outcome_labels: dict[str, object] | None = None,
        postmortem: dict[str, object] | None = None,
        observed_events: list[dict[str, object]] | None = None,
        event_ticker_edges: list[dict[str, object]] | None = None,
        lessons: list[dict[str, object]] | None = None,
        counterexamples: list[dict[str, object]] | None = None,
        misses: list[object] | None = None,
        eligibility_matrix: object | None = None,
        outcome_coverage_status: object | None = None,
        blind_integrity: object | None = None,
        blind_seal_receipt: object | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": schema_version,
            "episode_id": episode_id,
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": created_at,
            "research_version": research_version,
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": (
                {"source": "test"} if price_source_snapshot is None else price_source_snapshot
            ),
            "blind_analysis": {
                "summary": "Accepted episode with identity metadata.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [
                    {
                        "source_id": "SRC-blind-analysis",
                        "source_type": "daily_blind_analysis_blind_analysis",
                        "uri": "prompt://daily_blind_analysis/test",
                    }
                ],
            },
            "blind_predictions": [] if blind_predictions is None else blind_predictions,
            "outcome_labels": {} if outcome_labels is None else outcome_labels,
            "observed_events": [] if observed_events is None else observed_events,
            "event_ticker_edges": [] if event_ticker_edges is None else event_ticker_edges,
            "lessons": [] if lessons is None else lessons,
            "counterexamples": [] if counterexamples is None else counterexamples,
            "misses": [] if misses is None else misses,
            "provenance": [
                {
                    "source_id": "SRC-episode",
                    "source_type": "accepted_research_episode",
                    "uri": "prompt://accepted_episode/test",
                }
            ],
            "available_from": "2030-01-11T00:00:00+09:00",
        }
        if execution_protocol_version is not None:
            payload["execution_protocol_version"] = execution_protocol_version
        if eligibility_matrix is not None:
            payload["eligibility_matrix"] = eligibility_matrix
        if outcome_coverage_status is not None:
            payload["outcome_coverage_status"] = outcome_coverage_status
        if blind_integrity is not None:
            payload["blind_integrity"] = blind_integrity
        if blind_seal_receipt is not None:
            payload["blind_seal_receipt"] = blind_seal_receipt
        if postmortem is not None:
            payload["postmortem"] = postmortem
        return payload

    episode_path = tmp_path / "research" / "accepted" / "EP-identity.json"
    write_json(episode_path, episode_payload("EP-identity"))

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]

    write_json(episode_path, episode_payload("EP-other"))
    mismatch = audit_provenance(tmp_path)

    assert not mismatch["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "filename/episode_id mismatch"
    ) in mismatch["findings"]

    write_json(episode_path, episode_payload("EP-identity", schema_version="bad.version"))
    schema_failed = audit_provenance(tmp_path)

    assert not schema_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode schema_version invalid"
    ) in schema_failed["findings"]

    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            created_at="not-a-timestamp",
            research_version="",
            price_source_snapshot={},
        ),
    )
    metadata_failed = audit_provenance(tmp_path)

    assert not metadata_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "created_at missing or invalid"
    ) in metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "research_version missing or invalid"
    ) in metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "price_source_snapshot missing or invalid"
    ) in metadata_failed["findings"]

    missing_blind_shape = episode_payload("EP-identity")
    blind_analysis = missing_blind_shape["blind_analysis"]
    assert isinstance(blind_analysis, dict)
    blind_analysis["summary"] = ""
    blind_analysis["open_world_mechanisms"] = []
    blind_analysis["initial_uncertainties"] = [123]
    write_json(episode_path, missing_blind_shape)
    blind_shape_failed = audit_provenance(tmp_path)

    assert not blind_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "blind_analysis summary missing or invalid"
    ) in blind_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "blind_analysis open_world_mechanisms missing or invalid"
    ) in blind_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "blind_analysis initial_uncertainties invalid"
    ) in blind_shape_failed["findings"]

    valid_candidate = {
        "rank": 1,
        "ticker": "UNKNOWN",
        "company_name": "CandidateCo",
        "path_type": "SINGLE_EVENT",
        "event_ids": ["EVT-identity"],
        "thesis": "Current evidence creates a blind-safe candidate hypothesis.",
        "why_now": "The candidate is tied to the current pre-cutoff event.",
        "confidence_label": "speculative",
        "evidence_quality": "low",
        "causal_chain": ["current event", "blind-safe candidate hypothesis"],
        "direct_evidence": ["current-news mention"],
        "inferred_evidence": ["open-world mechanism"],
        "market_memory_evidence": [],
        "prior_positive_cases": [],
        "prior_negative_cases": [],
        "counterarguments": ["listing status may be unverified"],
        "disconfirming_conditions": ["cutoff-after evidence only"],
        "source_urls": ["news://EVT-identity"],
        "memory_episode_ids": [],
        "provenance": [
            {
                "source_id": "SRC-candidate",
                "source_type": "daily_blind_analysis_candidate",
                "uri": "candidate://daily_blind_analysis/test/1",
            }
        ],
    }
    write_json(
        episode_path,
        episode_payload("EP-identity", blind_predictions=[valid_candidate]),
    )
    valid_prediction = audit_provenance(tmp_path)

    assert valid_prediction["passed"], valid_prediction["findings"]

    invalid_candidate = {
        **valid_candidate,
        "rank": 0,
        "ticker": "",
        "path_type": "STATIC_MAP",
        "why_now": "",
        "confidence_label": "73%",
        "evidence_quality": "unknownish",
        "direct_evidence": ["valid", 123],
    }
    write_json(
        episode_path,
        episode_payload("EP-identity", blind_predictions=[invalid_candidate]),
    )
    candidate_shape_failed = audit_provenance(tmp_path)

    assert not candidate_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "rank missing or invalid"
    ) in candidate_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "ticker missing or invalid"
    ) in candidate_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "path_type missing or invalid"
    ) in candidate_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "why_now missing or invalid"
    ) in candidate_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "confidence_label missing or invalid"
    ) in candidate_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "evidence_quality missing or invalid"
    ) in candidate_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction 1 "
        "direct_evidence missing or invalid"
    ) in candidate_shape_failed["findings"]

    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            blind_predictions=[
                {**valid_candidate, "rank": 2},
                {**valid_candidate, "rank": 2},
            ],
        ),
    )
    rank_shape_failed = audit_provenance(tmp_path)

    assert not rank_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode blind prediction "
        "ranks are not sequential"
    ) in rank_shape_failed["findings"]

    valid_postmortem = {
        "summary": "Evaluation-only postmortem learning.",
        "hits": ["CandidateCo"],
        "misses": [],
        "false_positives": [],
        "failure_codes": ["UNKNOWN"],
        "lessons": ["Use only after the evaluated trade date."],
        "provenance": [
            {
                "source_id": "SRC-postmortem",
                "source_type": "evaluation_postmortem",
                "uri": "prompt://postmortem/test",
            }
        ],
    }
    write_json(
        episode_path,
        episode_payload("EP-identity", postmortem=valid_postmortem),
    )
    valid_postmortem_result = audit_provenance(tmp_path)

    assert valid_postmortem_result["passed"], valid_postmortem_result["findings"]

    valid_outcome_labels = {
        "UNKNOWN": {
            "open_gap_pct": 1.5,
            "intraday_high_return_pct": 8.0,
            "close_return_pct": None,
            "upper_limit_touched": False,
            "upper_limit_closed": False,
            "upper_limit_released": None,
            "one_price_upper_limit": None,
            "volume": 1000.0,
            "amount": 2500000.0,
            "turnover_ratio": 0.2,
            "market_cap_previous_close": 100000000.0,
            "intraday_fields_unavailable": ["upper_limit_first_touch_time"],
            "flags": ["daily_only"],
        }
    }
    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            outcome_labels=valid_outcome_labels,
            misses=["MISSED-UNKNOWN"],
        ),
    )
    valid_outcome_result = audit_provenance(tmp_path)

    assert valid_outcome_result["passed"], valid_outcome_result["findings"]

    valid_eligibility_matrix = {
        "forecast_evaluation_eligible": True,
        "direct_supervised_cases_eligible": False,
        "theme_supervised_cases_eligible": False,
        "leader_pair_training_eligible": False,
        "retrospective_memory_eligible": True,
        "brain_eligible": True,
        "reasons": {
            "direct_supervised_cases_eligible": "candidate outcomes are unresolved",
        },
    }
    valid_blind_integrity = {
        "blind_context_mode": "NEWS_ONLY_STRICT",
        "blind_web_search_call_count": 0,
        "blind_price_repository_access_count": 0,
        "blind_current_price_access_count": 0,
        "no_d_outcome_exposed": True,
    }
    valid_blind_seal_receipt = {
        "schema_version": "nslab.blind_seal_receipt.v1",
        "phase": "BLIND_SEALED",
        "blind_artifact_sha256": "a" * 64,
        "no_d_outcome_exposed": True,
    }
    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            execution_protocol_version="nslab.exhaustive_news_blind_full_market.v5",
            eligibility_matrix=valid_eligibility_matrix,
            outcome_coverage_status="PREDICTED_CANDIDATES_ONLY",
            blind_integrity=valid_blind_integrity,
            blind_seal_receipt=valid_blind_seal_receipt,
        ),
    )
    valid_execution_metadata_result = audit_provenance(tmp_path)

    assert (
        valid_execution_metadata_result["passed"]
    ), valid_execution_metadata_result["findings"]

    invalid_eligibility_matrix = {
        "forecast_evaluation_eligible": "yes",
        "reasons": {"": ""},
    }
    invalid_blind_integrity = {
        "blind_context_mode": "",
        "blind_web_search_call_count": -1,
        "blind_price_repository_access_count": True,
        "blind_current_price_access_count": 1,
        "no_d_outcome_exposed": False,
    }
    invalid_blind_seal_receipt = {
        "schema_version": "bad.version",
        "phase": "POSTMORTEM",
        "blind_artifact_sha256": "",
        "no_d_outcome_exposed": False,
    }
    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            execution_protocol_version="",
            eligibility_matrix=invalid_eligibility_matrix,
            outcome_coverage_status="",
            blind_integrity=invalid_blind_integrity,
            blind_seal_receipt=invalid_blind_seal_receipt,
        ),
    )
    execution_metadata_failed = audit_provenance(tmp_path)

    assert not execution_metadata_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "execution_protocol_version invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode "
        "outcome_coverage_status invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode eligibility_matrix "
        "forecast_evaluation_eligible invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode eligibility_matrix "
        "reasons invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_integrity "
        "blind_context_mode invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_integrity "
        "blind_current_price_access_count must be zero"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_integrity "
        "no_d_outcome_exposed must be true"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_seal_receipt "
        "schema_version invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_seal_receipt "
        "phase invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_seal_receipt "
        "blind_artifact_sha256 invalid"
    ) in execution_metadata_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode blind_seal_receipt "
        "no_d_outcome_exposed must be true"
    ) in execution_metadata_failed["findings"]

    invalid_outcome_labels = {
        "": {},
        "UNKNOWN": {
            "open_gap_pct": "1.5",
            "upper_limit_touched": "false",
            "intraday_fields_unavailable": ["valid", 1],
            "flags": "daily_only",
        },
    }
    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            outcome_labels=invalid_outcome_labels,
            misses=["valid", 1],
        ),
    )
    outcome_shape_failed = audit_provenance(tmp_path)

    assert not outcome_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode outcome label key "
        "missing or invalid"
    ) in outcome_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode outcome label UNKNOWN "
        "open_gap_pct invalid"
    ) in outcome_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode outcome label UNKNOWN "
        "upper_limit_touched invalid"
    ) in outcome_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode outcome label UNKNOWN "
        "intraday_fields_unavailable missing or invalid"
    ) in outcome_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode outcome label UNKNOWN "
        "flags missing or invalid"
    ) in outcome_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode misses missing or invalid"
    ) in outcome_shape_failed["findings"]

    invalid_postmortem = {
        **valid_postmortem,
        "summary": "",
        "hits": ["valid", 1],
        "failure_codes": ["NOT_A_FAILURE_CODE"],
    }
    write_json(
        episode_path,
        episode_payload("EP-identity", postmortem=invalid_postmortem),
    )
    postmortem_shape_failed = audit_provenance(tmp_path)

    assert not postmortem_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode postmortem "
        "summary missing or invalid"
    ) in postmortem_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode postmortem "
        "hits missing or invalid"
    ) in postmortem_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode postmortem "
        "failure_codes invalid"
    ) in postmortem_shape_failed["findings"]

    valid_lesson = {
        "claim_id": "CL-identity",
        "statement": "Use the lesson only after evaluation availability.",
        "mechanism": "postmortem learning from sealed blind prediction",
        "scope": "postmortem evaluation learning",
        "conditions": ["available only after the evaluated trade date"],
        "failure_modes": ["UNKNOWN"],
        "support_episode_ids": ["EP-identity"],
        "contradiction_episode_ids": [],
        "near_miss_episode_ids": [],
        "status": "tentative",
        "confidence_label": "medium",
        "first_observed_at": "2030-01-10",
        "last_updated_at": "2030-01-11T00:00:00+09:00",
        "available_from": "2030-01-11T00:00:00+09:00",
        "provenance": [
            {
                "source_id": "SRC-lesson",
                "source_type": "evaluation_postmortem",
                "uri": "prompt://lesson/test",
            }
        ],
    }
    write_json(
        episode_path,
        episode_payload("EP-identity", lessons=[valid_lesson]),
    )
    valid_lesson_result = audit_provenance(tmp_path)

    assert valid_lesson_result["passed"], valid_lesson_result["findings"]

    invalid_lesson = {
        **valid_lesson,
        "claim_id": "",
        "statement": "",
        "conditions": ["valid", 1],
        "status": "unsupported",
        "confidence_label": "90%",
        "first_observed_at": "not-a-date",
        "last_updated_at": "not-a-datetime",
    }
    write_json(
        episode_path,
        episode_payload("EP-identity", lessons=[invalid_lesson]),
    )
    lesson_shape_failed = audit_provenance(tmp_path)

    assert not lesson_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "claim_id missing or invalid"
    ) in lesson_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "statement missing or invalid"
    ) in lesson_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "conditions missing or invalid"
    ) in lesson_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "status missing or invalid"
    ) in lesson_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "confidence_label missing or invalid"
    ) in lesson_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "first_observed_at invalid"
    ) in lesson_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode lesson 1 "
        "last_updated_at invalid"
    ) in lesson_shape_failed["findings"]

    valid_event = {
        "event_id": "EVT-identity",
        "row_number": 1,
        "published_at": "2030-01-10T08:00:00+09:00",
        "collected_at": "2030-01-10T08:01:00+09:00",
        "title": "Accepted observed event",
        "body": "Observed before cutoff.",
        "source_id": "SRC-observed-event",
        "provenance": [
            {
                "source_id": "SRC-observed-event",
                "source_type": "accepted_observed_event",
                "uri": "prompt://observed_event/test",
            }
        ],
    }
    valid_edge = {
        "edge_id": "EDGE-identity",
        "episode_id": "EP-identity",
        "event_id": "EVT-identity",
        "ticker": "UNKNOWN",
        "company_name": "CandidateCo",
        "relation_class": "DIRECT",
        "relation_explanation": "The edge records an observed direct relation.",
        "directly_mentioned": True,
        "fundamental_evidence": ["current event relation"],
        "narrative_evidence": [],
        "market_memory_evidence": [],
        "temporal_validity": "pre-cutoff",
        "confidence_label": "medium",
        "provenance": [
            {
                "source_id": "SRC-edge",
                "source_type": "accepted_event_ticker_edge",
                "uri": "prompt://edge/test",
            }
        ],
    }
    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            observed_events=[valid_event],
            event_ticker_edges=[valid_edge],
        ),
    )
    valid_event_edge_result = audit_provenance(tmp_path)

    assert valid_event_edge_result["passed"], valid_event_edge_result["findings"]

    invalid_event = {
        **valid_event,
        "event_id": "",
        "row_number": 0,
        "published_at": "not-a-datetime",
        "collected_at": "not-a-datetime",
    }
    invalid_edge = {
        **valid_edge,
        "edge_id": "",
        "relation_class": "STATIC_MAP",
        "directly_mentioned": "yes",
        "confidence_label": "certain",
        "fundamental_evidence": ["valid", 1],
    }
    write_json(
        episode_path,
        episode_payload(
            "EP-identity",
            observed_events=[invalid_event],
            event_ticker_edges=[invalid_edge],
        ),
    )
    event_edge_shape_failed = audit_provenance(tmp_path)

    assert not event_edge_shape_failed["passed"]
    assert (
        "research/accepted/EP-identity.json: research episode observed event 1 "
        "event_id missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode observed event 1 "
        "row_number missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode observed event 1 "
        "published_at missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode observed event 1 "
        "collected_at invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode event ticker edge 1 "
        "edge_id missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode event ticker edge 1 "
        "relation_class missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode event ticker edge 1 "
        "directly_mentioned missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode event ticker edge 1 "
        "confidence_label missing or invalid"
    ) in event_edge_shape_failed["findings"]
    assert (
        "research/accepted/EP-identity.json: research episode event ticker edge 1 "
        "fundamental_evidence missing or invalid"
    ) in event_edge_shape_failed["findings"]


def test_provenance_audit_validates_accepted_episode_top_level_sources(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "runs" / "checkpoints" / "evaluations" / "EP-accepted"
    source_path.mkdir(parents=True)
    sealed_prediction = source_path / "sealed_blind_prediction.json"
    sealed_prediction.write_text('{"prediction":"sealed"}\n', encoding="utf-8")
    episode_path = tmp_path / "research" / "accepted" / "EP-accepted.json"
    write_json(
        episode_path,
        {
            "schema_version": "nslab.research_episode.v1",
            "episode_id": "EP-accepted",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-11T00:00:00+09:00",
            "research_version": "evaluation-postmortem-v1",
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Accepted episode with sealed provenance.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [
                    {
                        "source_id": "SRC-blind-analysis",
                        "source_type": "daily_blind_analysis_blind_analysis",
                        "uri": "prompt://daily_blind_analysis/test",
                    }
                ],
            },
            "blind_predictions": [],
            "outcome_labels": {},
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [],
            "counterexamples": [],
            "misses": [],
            "provenance": [
                {
                    "source_id": "SRC-sealed",
                    "source_type": "sealed_blind_prediction",
                    "uri": sealed_prediction.relative_to(tmp_path).as_posix(),
                    "content_sha256": file_sha256(sealed_prediction),
                }
            ],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]
    assert result["checked_research_episode_files"] == 1

    tampered = read_json(episode_path)
    tampered["provenance"][0]["content_sha256"] = "0" * 64
    write_json(episode_path, tampered)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    assert (
        "research/accepted/EP-accepted.json: research episode provenance 1 "
        "content_sha256 mismatch"
    ) in failed["findings"]


def test_provenance_audit_validates_research_episode_input_news_hash(
    tmp_path: Path,
) -> None:
    news_path = tmp_path / "data" / "inbox" / "news" / "accepted_news.csv"
    news_path.parent.mkdir(parents=True)
    news_path.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-10","08:00:00","Accepted input","Source-backed news."\n',
        encoding="utf-8",
    )
    episode_path = tmp_path / "research" / "accepted" / "EP-news.json"
    write_json(
        episode_path,
        {
            "schema_version": "nslab.research_episode.v1",
            "episode_id": "EP-news",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-11T00:00:00+09:00",
            "research_version": "input-news-test-v1",
            "input_news_files": [news_path.relative_to(tmp_path).as_posix()],
            "input_news_hashes": [file_sha256(news_path)],
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Accepted episode with input news hash.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [
                    {
                        "source_id": "SRC-blind-analysis",
                        "source_type": "daily_blind_analysis_blind_analysis",
                        "uri": "prompt://daily_blind_analysis/test",
                    }
                ],
            },
            "blind_predictions": [],
            "outcome_labels": {},
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [],
            "counterexamples": [],
            "misses": [],
            "provenance": [
                {
                    "source_id": "SRC-episode",
                    "source_type": "accepted_research_episode",
                    "uri": "prompt://accepted_episode/test",
                }
            ],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]
    assert result["checked_research_episode_files"] == 1

    tampered = read_json(episode_path)
    tampered["input_news_hashes"][0] = "0" * 64
    write_json(episode_path, tampered)

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    assert (
        "research/accepted/EP-news.json: research episode input news hash mismatch: "
        "data/inbox/news/accepted_news.csv"
    ) in failed["findings"]


def test_provenance_audit_rejects_early_research_episode_available_from(
    tmp_path: Path,
) -> None:
    episode_path = tmp_path / "research" / "accepted" / "EP-early.json"
    write_json(
        episode_path,
        {
            "schema_version": "nslab.research_episode.v1",
            "episode_id": "EP-early",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-11T00:00:00+09:00",
            "research_version": "available-from-test-v1",
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Accepted episode with early available_from.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [
                    {
                        "source_id": "SRC-blind-analysis",
                        "source_type": "daily_blind_analysis_blind_analysis",
                        "uri": "prompt://daily_blind_analysis/test",
                    }
                ],
            },
            "blind_predictions": [],
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [],
            "counterexamples": [],
            "misses": [],
            "provenance": [
                {
                    "source_id": "SRC-episode",
                    "source_type": "accepted_research_episode",
                    "uri": "prompt://accepted_episode/test",
                }
            ],
            "available_from": "2030-01-10T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "research/accepted/EP-early.json: research episode available_from "
        "precedes next trading day"
    ) in result["findings"]


def test_provenance_audit_rejects_research_episode_cutoff_after_preopen(
    tmp_path: Path,
) -> None:
    write_json(
        tmp_path / "research" / "accepted" / "EP-cutoff-leak.json",
        {
            "schema_version": "nslab.research_episode.v1",
            "episode_id": "EP-cutoff-leak",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T09:00:00+09:00",
            "created_at": "2030-01-11T00:00:00+09:00",
            "research_version": "cutoff-test-v1",
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Accepted episode with late cutoff.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [
                    {
                        "source_id": "SRC-blind-analysis",
                        "source_type": "daily_blind_analysis_blind_analysis",
                        "uri": "prompt://daily_blind_analysis/test",
                    }
                ],
            },
            "blind_predictions": [],
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [],
            "counterexamples": [],
            "misses": [],
            "provenance": [
                {
                    "source_id": "SRC-episode",
                    "source_type": "accepted_research_episode",
                    "uri": "prompt://accepted_episode/test",
                }
            ],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "research/accepted/EP-cutoff-leak.json: research episode cutoff_at "
        "is after trade-date cutoff"
    ) in result["findings"]


def test_provenance_audit_rejects_early_research_episode_claim_available_from(
    tmp_path: Path,
) -> None:
    write_json(
        tmp_path / "research" / "accepted" / "EP-claim-time.json",
        {
            "schema_version": "nslab.research_episode.v1",
            "episode_id": "EP-claim-time",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-11T00:00:00+09:00",
            "research_version": "claim-available-from-test-v1",
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Accepted episode with early claim availability.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [
                    {
                        "source_id": "SRC-blind-analysis",
                        "source_type": "daily_blind_analysis_blind_analysis",
                        "uri": "prompt://daily_blind_analysis/test",
                    }
                ],
            },
            "blind_predictions": [],
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [
                {
                    "claim_id": "CL-early-lesson",
                    "available_from": "2030-01-10T09:00:00+09:00",
                    "provenance": [
                        {
                            "source_id": "SRC-lesson",
                            "source_type": "evaluation_postmortem",
                            "uri": "prompt://lesson/test",
                        }
                    ],
                }
            ],
            "counterexamples": [
                {
                    "claim_id": "CL-early-counterexample",
                    "available_from": "2030-01-10T09:00:00+09:00",
                    "provenance": [
                        {
                            "source_id": "SRC-counterexample",
                            "source_type": "evaluation_postmortem",
                            "uri": "prompt://counterexample/test",
                        }
                    ],
                }
            ],
            "misses": [],
            "provenance": [
                {
                    "source_id": "SRC-episode",
                    "source_type": "accepted_research_episode",
                    "uri": "prompt://accepted_episode/test",
                }
            ],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "research/accepted/EP-claim-time.json: research episode lesson 1 "
        "available_from precedes episode"
    ) in result["findings"]
    assert (
        "research/accepted/EP-claim-time.json: research episode counterexample 1 "
        "available_from precedes episode"
    ) in result["findings"]


def test_provenance_audit_requires_accepted_episode_blind_decision_provenance(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "runs" / "checkpoints" / "evaluations" / "EP-blind"
    source_path.mkdir(parents=True)
    sealed_prediction = source_path / "sealed_blind_prediction.json"
    sealed_prediction.write_text('{"prediction":"sealed"}\n', encoding="utf-8")
    write_json(
        tmp_path / "research" / "accepted" / "EP-blind.json",
        {
            "schema_version": "nslab.research_episode.v1",
            "episode_id": "EP-blind",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-11T00:00:00+09:00",
            "research_version": "evaluation-postmortem-v1",
            "input_news_files": [],
            "input_news_hashes": [],
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Missing nested provenance.",
                "open_world_mechanisms": ["current evidence -> open-world blind mechanism"],
                "initial_uncertainties": [],
                "provenance": [],
            },
            "blind_predictions": [
                {
                    "company_name": "CandidateWithoutSource",
                    "provenance": [],
                    "event_ids": [],
                    "memory_episode_ids": [],
                    "source_urls": [],
                }
            ],
            "observed_events": [{"event_id": "EVT-missing", "provenance": []}],
            "event_ticker_edges": [{"edge_id": "EDGE-missing", "provenance": []}],
            "postmortem": {
                "summary": "Missing postmortem provenance.",
                "hits": [],
                "misses": [],
                "false_positives": [],
                "failure_codes": [],
                "lessons": [],
                "provenance": [],
            },
            "lessons": [{"claim_id": "CL-missing-lesson", "provenance": []}],
            "counterexamples": [{"claim_id": "CL-missing-counterexample", "provenance": []}],
            "misses": [],
            "provenance": [
                {
                    "source_id": "SRC-sealed",
                    "source_type": "sealed_blind_prediction",
                    "uri": sealed_prediction.relative_to(tmp_path).as_posix(),
                    "content_sha256": file_sha256(sealed_prediction),
                }
            ],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "research/accepted/EP-blind.json: research episode blind_analysis "
        "missing provenance"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode blind prediction 1 "
        "missing provenance"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode blind prediction "
        "lacks provenance anchors: CandidateWithoutSource"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode postmortem "
        "missing provenance"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode observed event 1 "
        "missing provenance"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode event ticker edge 1 "
        "missing provenance"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode lesson 1 "
        "missing provenance"
    ) in result["findings"]
    assert (
        "research/accepted/EP-blind.json: research episode counterexample 1 "
        "missing provenance"
    ) in result["findings"]


def test_provenance_audit_requires_blind_sector_and_candidate_provenance(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": {"summary": "No provenance."},
            "dominant_sectors": [
                {
                    "name": "SectorWithoutProvenance",
                    "formation_mechanism": "missing source",
                    "expected_breadth": "unknown",
                }
            ],
            "candidates": [{"company_name": "CandidateCo", "event_ids": ["EVT-1"]}],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "def456"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "2030-01-10.json: blind_analysis missing provenance" in result["findings"]
    assert (
        "2030-01-10.json: dominant sector missing provenance: SectorWithoutProvenance"
    ) in result["findings"]
    assert (
        "2030-01-10.json: dominant sector lacks provenance anchors: SectorWithoutProvenance"
    ) in result["findings"]
    assert (
        "2030-01-10.json: candidate missing provenance: CandidateCo"
    ) in result["findings"]


def test_provenance_audit_validates_company_memory_source_hash(tmp_path: Path) -> None:
    source_dir = tmp_path / "runs" / "company_memory_sources"
    source_dir.mkdir(parents=True)
    source_path = source_dir / "prediction-source.json"
    source_path.write_text('{"candidate":"NovelCo"}\n', encoding="utf-8")
    memory_dir = tmp_path / "memory" / "company_memory"
    memory_dir.mkdir(parents=True)
    write_json(
        memory_dir / "CM-valid.json",
        {
            "ticker": "UNKNOWN",
            "company_name": "NovelCo",
            "aliases": ["NovelCo"],
            "business_descriptions": ["Generated from a blind candidate."],
            "locations": [],
            "customers": [],
            "supply_chain_roles": ["current evidence", "company verification"],
            "prior_market_narratives": ["Blind thesis."],
            "prior_leader_occurrences": [],
            "contradictory_relations": ["listing status unverified"],
            "known_at": "2030-01-10T08:59:59+09:00",
            "provenance": [
                {
                    "source_id": "SRC-company-memory",
                    "source_type": "blind_analysis_company_memory_candidate",
                    "uri": "runs/company_memory_sources/prediction-source.json",
                    "content_sha256": file_sha256(source_path),
                    "excerpt": "Blind thesis.",
                    "observed_at": "2030-01-10T08:59:59+09:00",
                }
            ],
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"] is True
    assert result["checked_company_memory_files"] == 1

    source_path.write_text('{"candidate":"Tampered"}\n', encoding="utf-8")
    failed = audit_provenance(tmp_path)

    assert failed["passed"] is False
    assert (
        "memory/company_memory/CM-valid.json: company memory provenance 1 content_sha256 mismatch"
    ) in failed["findings"]


def test_provenance_audit_validates_mechanism_memory_source_hash(tmp_path: Path) -> None:
    source_dir = tmp_path / "reports"
    source_dir.mkdir()
    source_path = source_dir / "2030-01-10_postmortem.json"
    source_path.write_text('{"lesson":"Mechanism source"}\n', encoding="utf-8")
    mechanisms_dir = tmp_path / "memory" / "mechanisms" / "current"
    mechanisms_dir.mkdir(parents=True)
    mechanism = {
        "mechanism_id": "MM-valid",
        "natural_language_description": "Current event mechanics require source-backed review.",
        "successful_cases": ["EP-valid"],
        "provenance": [
            {
                "source_id": "SRC-mechanism-memory",
                "source_type": "evaluation_postmortem",
                "uri": "reports/2030-01-10_postmortem.json",
                "content_sha256": file_sha256(source_path),
                "observed_at": "2030-01-11T00:00:00+09:00",
            }
        ],
    }
    (mechanisms_dir / "mechanisms.jsonl").write_text(
        canonical_json(mechanism) + "\n",
        encoding="utf-8",
    )

    result = audit_provenance(tmp_path)

    assert result["passed"] is True
    assert result["checked_mechanism_memory_records"] == 1

    source_path.write_text('{"lesson":"Tampered mechanism source"}\n', encoding="utf-8")
    failed = audit_provenance(tmp_path)

    assert failed["passed"] is False
    assert (
        "memory/mechanisms/current/mechanisms.jsonl:1: "
        "mechanism memory provenance 1 content_sha256 mismatch"
    ) in failed["findings"]


def test_provenance_audit_validates_evaluation_episode_sources(tmp_path: Path) -> None:
    checkpoint_dir = tmp_path / "runs" / "checkpoints" / "evaluations" / "EP-evaluation"
    prediction_source = checkpoint_dir / "sealed_blind_prediction.json"
    blind_analysis = {
        "summary": "Sealed blind analysis.",
    }
    write_json(
        prediction_source,
        {
            "schema_version": "nslab.blind_prediction.v1",
            "prediction_id": "PRED-eval",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-10T08:58:00+09:00",
            "sealed_at": "2030-01-10T08:58:30+09:00",
            "blind_artifact_sha256": "a" * 64,
            "blind_analysis": blind_analysis,
            "dominant_sectors": [],
            "candidates": [],
        },
    )
    prediction_sha256 = file_sha256(prediction_source)
    report_path = checkpoint_dir / "postmortem_report.json"
    postmortem = {
        "summary": "Evaluation learning from sealed blind prediction.",
        "hits": ["HitCo"],
        "misses": [],
        "false_positives": [],
        "failure_codes": ["UNKNOWN"],
        "lessons": ["Use only after the evaluated trade date."],
        "provenance": [],
    }
    eligibility = {
        "forecast_evaluation_eligible": True,
        "direct_supervised_cases_eligible": True,
        "theme_supervised_cases_eligible": False,
        "leader_pair_training_eligible": False,
        "retrospective_memory_eligible": True,
        "brain_eligible": True,
        "reasons": {},
    }
    postmortem_prompt_sha256 = "evaluation-postmortem-hash"
    postmortem_model_config = {
        "configured_provider": "mock",
        "provider_class": "DeterministicMockLLMProvider",
    }
    write_json(
        report_path,
        {
            "schema_version": "nslab.evaluation.v1",
            "execution_protocol_version": "nslab.exhaustive_news_blind_full_market.v5",
            "trade_date": "2030-01-10",
            "blind_prediction_id": "PRED-eval",
            "blind_prediction_sha256": prediction_sha256,
            "outcome_coverage_status": "PREDICTED_CANDIDATES_ONLY",
            "outcomes": {},
            "performance_metrics": {"candidate_count": 0},
            "postmortem_prompt_version": "evaluation_postmortem.v1",
            "postmortem_prompt_sha256": postmortem_prompt_sha256,
            "postmortem_model_config": postmortem_model_config,
            "postmortem": postmortem,
            "eligibility_matrix": eligibility,
        },
    )
    trace_payload = _trace_payload(
        prompt_sha256=postmortem_prompt_sha256,
        purpose="evaluation_postmortem",
        response_model="Postmortem",
        prompt_version="evaluation_postmortem.v1",
        model_config=postmortem_model_config,
        output=postmortem,
        checkpoint_id="LLMCKPT-evaluation-postmortem",
        trace_id="TRACE-evaluation-postmortem",
    )
    trace_dir = tmp_path / "runs" / "traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    write_json(trace_dir / "TRACE-evaluation-postmortem.json", trace_payload)
    _write_trace_checkpoint(tmp_path, trace_payload)
    evaluation_provenance = {
        "source_id": "SRC-evaluation",
        "source_type": "evaluation_postmortem",
        "uri": "runs/checkpoints/evaluations/EP-evaluation/postmortem_report.json",
        "content_sha256": file_sha256(report_path),
        "observed_at": "2030-01-11T00:00:00+09:00",
    }
    prediction_provenance = {
        "source_id": "SRC-sealed",
        "source_type": "sealed_blind_prediction",
        "uri": "runs/checkpoints/evaluations/EP-evaluation/sealed_blind_prediction.json",
        "content_sha256": file_sha256(prediction_source),
        "observed_at": "2030-01-10T08:59:30+09:00",
    }
    episode_path = tmp_path / "research" / "episodes" / "EP-evaluation.json"
    write_json(
        episode_path,
        {
            "episode_id": "EP-evaluation",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-11T00:01:00+09:00",
            "execution_protocol_version": "nslab.exhaustive_news_blind_full_market.v5",
            "research_version": "evaluation-postmortem-v1",
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                **blind_analysis,
                "provenance": [prediction_provenance],
            },
            "blind_predictions": [],
            "outcome_labels": {},
            "postmortem": postmortem,
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [],
            "counterexamples": [],
            "misses": [],
            "eligibility_matrix": eligibility,
            "outcome_coverage_status": "PREDICTED_CANDIDATES_ONLY",
            "provenance": [prediction_provenance, evaluation_provenance],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"] is True, result["findings"]
    assert result["checked_evaluation_episode_files"] == 1

    report = read_json(report_path)
    report["postmortem"]["summary"] = "Tampered postmortem."
    write_json(report_path, report)
    failed = audit_provenance(tmp_path)

    assert failed["passed"] is False
    assert (
        "research/episodes/EP-evaluation.json: evaluation postmortem provenance 2 "
        "content_sha256 mismatch"
    ) in failed["findings"]
    assert (
        "research/episodes/EP-evaluation.json: evaluation report postmortem mismatch"
    ) in failed["findings"]

    write_json(report_path, read_json(report_path) | {"postmortem": postmortem})
    report = read_json(report_path)
    report["postmortem_prompt_sha256"] = "missing-evaluation-trace-hash"
    write_json(report_path, report)
    failed_trace = audit_provenance(tmp_path)

    assert (
        "research/episodes/EP-evaluation.json: evaluation postmortem prompt hash "
        "has no matching trace"
    ) in failed_trace["findings"]

    report["postmortem_prompt_sha256"] = postmortem_prompt_sha256
    write_json(report_path, report)
    mismatched_trace_payload = json.loads(json.dumps(trace_payload))
    mismatched_trace_payload["output"]["summary"] = "Different trace postmortem."
    mismatched_trace_payload["output_sha256"] = sha256_text(
        canonical_json(mismatched_trace_payload["output"])
    )
    write_json(trace_dir / "TRACE-evaluation-postmortem.json", mismatched_trace_payload)
    _write_trace_checkpoint(tmp_path, mismatched_trace_payload)

    failed_trace_output = audit_provenance(tmp_path)

    assert (
        "research/episodes/EP-evaluation.json: evaluation postmortem trace "
        "summary mismatch"
    ) in failed_trace_output["findings"]

    write_json(trace_dir / "TRACE-evaluation-postmortem.json", trace_payload)
    _write_trace_checkpoint(tmp_path, trace_payload)

    episode = read_json(episode_path)
    episode["available_from"] = "2030-01-10T00:00:00+09:00"
    write_json(episode_path, episode)
    failed_available_from = audit_provenance(tmp_path)

    assert (
        "research/episodes/EP-evaluation.json: evaluation available_from is not "
        "next trading day"
    ) in failed_available_from["findings"]


def test_provenance_audit_flags_prompt_hash_without_matching_trace(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "manifest-prompt-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    write_json(
        tmp_path / "runs" / "traces" / "TRACE-daily.json",
        _trace_payload(prompt_sha256="different-trace-hash"),
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: prompt hash has no matching trace for daily_blind_analysis"
    ) in result["findings"]


def test_provenance_audit_flags_current_manifest_missing_pre_analysis_trace(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-linked",
            "model_config": {"provider": "mock"},
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "news_novelty_review": "novelty-hash",
            },
            "token_counts": {
                "blind_analysis_prompt": 25,
                "news_novelty_review_prompt": 25,
            },
            "truncations": [],
            "web_queries": [],
            "web_sources": [],
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    write_json(
        tmp_path / "runs" / "traces" / "TRACE-daily.json",
        _trace_payload(prompt_sha256="blind-hash"),
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: prompt hash has no matching trace for news_novelty_review"
    ) in result["findings"]


def test_provenance_audit_flags_trace_model_config_mismatch(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "model_config": {
                "configured_provider": "mock",
                "provider_class": "DeterministicMockLLMProvider",
                "max_concurrency": 4,
                "shard_episode_count": 20,
            },
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    trace = _trace_payload(prompt_sha256="blind-hash")
    trace["model_config"] = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "max_concurrency": 4,
        "shard_episode_count": 20,
    }
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", trace)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: trace model_config mismatch for daily_blind_analysis: "
        "configured_provider, provider_class"
    ) in result["findings"]


def test_provenance_audit_flags_trace_prompt_token_count_mismatch(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "token_counts": {"blind_analysis_prompt": 26},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    write_json(
        tmp_path / "runs" / "traces" / "TRACE-daily.json",
        _trace_payload(prompt_sha256="blind-hash"),
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: trace prompt token count mismatch for "
        "daily_blind_analysis: TRACE-daily.json"
    ) in result["findings"]


def test_provenance_audit_flags_pre_analysis_trace_prompt_token_count_mismatch(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"news_novelty_review": "novelty-hash"},
            "token_counts": {"news_novelty_review_prompt": 26},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    novelty_trace = _trace_payload(prompt_sha256="novelty-hash")
    novelty_trace["purpose"] = "news_novelty_review"
    novelty_trace["metadata"] = {"prompt_version": "news_novelty_review.v1"}
    novelty_trace["prompt_version"] = "news_novelty_review.v1"
    write_json(tmp_path / "runs" / "traces" / "TRACE-novelty.json", novelty_trace)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: trace prompt token count mismatch for "
        "news_novelty_review: TRACE-novelty.json"
    ) in result["findings"]


def test_provenance_audit_accepts_current_trace_when_stale_trace_shares_prompt_hash(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    expected_model_config = {
        "configured_provider": "mock",
        "provider_class": "DeterministicMockLLMProvider",
        "max_concurrency": 4,
        "shard_episode_count": 20,
    }
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "model_config": expected_model_config,
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    stale_trace = _trace_payload(prompt_sha256="blind-hash")
    stale_trace["model_config"] = {
        "configured_provider": "openai",
        "provider_class": "OpenAIResponsesProvider",
        "max_concurrency": 4,
        "shard_episode_count": 20,
    }
    current_trace = _trace_payload(prompt_sha256="blind-hash")
    current_trace["model_config"] = expected_model_config
    write_json(tmp_path / "runs" / "traces" / "TRACE-stale.json", stale_trace)
    write_json(tmp_path / "runs" / "traces" / "TRACE-current.json", current_trace)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert (
        "2030-01-10.json: trace model_config mismatch for daily_blind_analysis: "
        "configured_provider, provider_class"
    ) not in result["findings"]


def test_provenance_audit_flags_incomplete_llm_trace_metadata(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    incomplete_trace = _trace_payload(prompt_sha256="blind-hash")
    incomplete_trace.pop("model_config")
    incomplete_trace.pop("prompt_version")
    incomplete_trace["schema_version"] = "bad.trace.schema"
    incomplete_trace["metadata"] = {"prompt_version": "different"}
    incomplete_trace["input_sha256"] = "wrong"
    incomplete_trace["token_usage"] = {}
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", incomplete_trace)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-daily.json: trace missing model_config" in result["findings"]
    assert "TRACE-daily.json: trace schema_version is invalid" in result["findings"]
    assert "TRACE-daily.json: trace missing prompt_version" in result["findings"]
    assert "TRACE-daily.json: trace metadata prompt_version mismatch" in result["findings"]
    assert "TRACE-daily.json: trace input_sha256 mismatch" in result["findings"]
    assert "TRACE-daily.json: trace missing prompt token estimate" in result["findings"]
    assert "TRACE-daily.json: trace missing completion token estimate" in result["findings"]


def test_provenance_audit_flags_invalid_llm_retry_error_history(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    trace = _trace_payload(prompt_sha256="blind-hash")
    trace["retries"] = 2
    trace["retry_errors"] = [{"type": "RuntimeError"}]
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", trace)
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-daily.json: trace retry_errors count mismatch" in result["findings"]
    assert "TRACE-daily.json: trace retry_errors item 0 missing message" in result["findings"]


def test_context_inspect_flags_invalid_llm_retry_error_history() -> None:
    trace = _trace_payload(prompt_sha256="blind-hash")
    trace["retries"] = 2
    trace["retry_errors"] = [{"type": "RuntimeError"}]

    errors = _llm_trace_payload_errors(trace)

    assert "retry_errors_count_mismatch" in errors
    assert "retry_errors_0_message_missing" in errors


def test_context_inspect_flags_invalid_embed_trace_output_summary() -> None:
    trace = {
        "schema_version": "nslab.llm_trace.v1",
        "trace_id": "TRACE-embed",
        "operation": "embed",
        "purpose": "trace.embed",
        "status": "ok",
        "provider": "DeterministicMockLLMProvider",
        "model_config": {"provider": "mock"},
        "input": {
            "texts_sha256": "abc123",
            "text_count": 1,
            "total_chars": 5,
        },
        "input_sha256": "",
        "output": {"vector_count": -1, "dimensions": True},
        "output_sha256": "",
        "checkpoint_id": "LLMCKPT-embed",
        "tool_calls": [],
        "retries": 0,
        "retry_errors": [],
        "token_usage": {"prompt_tokens_estimate": 1},
        "started_at": "2030-01-10T08:59:00+09:00",
        "finished_at": "2030-01-10T08:59:01+09:00",
    }
    trace["input_sha256"] = sha256_text(canonical_json(trace["input"]))
    trace["output_sha256"] = sha256_text(canonical_json(trace["output"]))

    errors = _llm_trace_payload_errors(trace)

    assert "embed_output_vector_count_invalid" in errors
    assert "embed_output_dimensions_invalid" in errors
    assert "embed_output_vectors_sha256_missing" in errors


def test_provenance_audit_flags_invalid_embed_trace_output_summary(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    (tmp_path / "runs" / "checkpoints" / "llm").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    trace = {
        "schema_version": "nslab.llm_trace.v1",
        "trace_id": "TRACE-embed",
        "operation": "embed",
        "purpose": "trace.embed",
        "status": "ok",
        "provider": "DeterministicMockLLMProvider",
        "model_config": {"provider": "mock"},
        "metadata": {},
        "input": {
            "texts_sha256": "abc123",
            "text_count": 1,
            "total_chars": 5,
        },
        "input_sha256": "",
        "output": {"vector_count": "1", "dimensions": -1},
        "output_sha256": "",
        "checkpoint_id": "LLMCKPT-embed",
        "tool_calls": [],
        "retries": 0,
        "retry_errors": [],
        "token_usage": {"prompt_tokens_estimate": 1},
        "started_at": "2030-01-10T08:59:00+09:00",
        "finished_at": "2030-01-10T08:59:01+09:00",
    }
    trace["input_sha256"] = sha256_text(canonical_json(trace["input"]))
    trace["output_sha256"] = sha256_text(canonical_json(trace["output"]))
    write_json(tmp_path / "runs" / "traces" / "TRACE-embed.json", trace)
    write_json(
        tmp_path / "runs" / "checkpoints" / "llm" / "LLMCKPT-embed.json",
        {
            "checkpoint_id": "LLMCKPT-embed",
            "schema_version": "nslab.llm_checkpoint.v1",
            "operation": "embed",
            "purpose": "trace.embed",
            "status": "ok",
            "provider": "DeterministicMockLLMProvider",
            "model_config": {"provider": "mock"},
            "metadata": {},
            "input": trace["input"],
            "input_sha256": trace["input_sha256"],
            "output": [[0.1, 0.2]],
            "output_sha256": sha256_text(canonical_json([[0.1, 0.2]])),
            "retries": 0,
            "retry_errors": [],
            "updated_at": "2030-01-10T08:59:01+09:00",
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-embed.json: trace embed output vector_count invalid" in result["findings"]
    assert "TRACE-embed.json: trace embed output dimensions invalid" in result["findings"]
    assert "TRACE-embed.json: trace embed output vectors_sha256 missing" in result["findings"]
    assert "TRACE-embed.json: trace checkpoint embedding output mismatch" in result["findings"]


def test_provenance_audit_flags_llm_checkpoint_mismatch(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    (tmp_path / "runs" / "checkpoints" / "llm").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    trace = _trace_payload(prompt_sha256="blind-hash")
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", trace)
    checkpoint_output = {"prediction_id": "PRED-tampered"}
    write_json(
        tmp_path / "runs" / "checkpoints" / "llm" / "LLMCKPT-linked.json",
        {
            "checkpoint_id": "LLMCKPT-linked",
            "schema_version": "nslab.llm_checkpoint.v1",
            "operation": trace["operation"],
            "purpose": trace["purpose"],
            "status": trace["status"],
            "provider": trace["provider"],
            "model_config": trace["model_config"],
            "metadata": trace["metadata"],
            "input": trace["input"],
            "input_sha256": trace["input_sha256"],
            "output": checkpoint_output,
            "output_sha256": sha256_text(canonical_json(checkpoint_output)),
            "token_usage": trace["token_usage"],
            "retries": trace["retries"],
            "retry_errors": trace["retry_errors"],
            "updated_at": "2030-01-10T08:59:01+09:00",
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-daily.json: trace checkpoint output mismatch" in result["findings"]
    assert "TRACE-daily.json: trace checkpoint output_sha256 mismatch" in result["findings"]


def test_provenance_audit_flags_llm_checkpoint_retry_error_mismatch(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    (tmp_path / "runs" / "checkpoints" / "llm").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    trace = _trace_payload(prompt_sha256="blind-hash")
    trace["retries"] = 1
    trace["retry_errors"] = [{"type": "RuntimeError", "message": "temporary failure"}]
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", trace)
    checkpoint = {
        "checkpoint_id": "LLMCKPT-linked",
        "schema_version": "nslab.llm_checkpoint.v1",
        "operation": trace["operation"],
        "purpose": trace["purpose"],
        "status": trace["status"],
        "provider": trace["provider"],
        "model_config": trace["model_config"],
        "metadata": trace["metadata"],
        "input": trace["input"],
        "input_sha256": trace["input_sha256"],
        "output": trace["output"],
        "output_sha256": trace["output_sha256"],
        "token_usage": trace["token_usage"],
        "retries": trace["retries"],
        "retry_errors": [{"type": "RuntimeError", "message": "different failure"}],
        "updated_at": "2030-01-10T08:59:01+09:00",
    }
    write_json(
        tmp_path / "runs" / "checkpoints" / "llm" / "LLMCKPT-linked.json",
        checkpoint,
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-daily.json: trace checkpoint retry_errors mismatch" in result["findings"]


def test_provenance_audit_flags_llm_checkpoint_token_usage_mismatch(
    tmp_path: Path,
) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "runs" / "traces").mkdir(parents=True)
    (tmp_path / "runs" / "checkpoints" / "llm").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {"blind_analysis": "blind-hash"},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
        },
    )
    trace = _trace_payload(prompt_sha256="blind-hash")
    write_json(tmp_path / "runs" / "traces" / "TRACE-daily.json", trace)
    checkpoint = {
        "checkpoint_id": "LLMCKPT-linked",
        "schema_version": "nslab.llm_checkpoint.v1",
        "operation": trace["operation"],
        "purpose": trace["purpose"],
        "status": trace["status"],
        "provider": trace["provider"],
        "model_config": trace["model_config"],
        "metadata": trace["metadata"],
        "input": trace["input"],
        "input_sha256": trace["input_sha256"],
        "output": trace["output"],
        "output_sha256": trace["output_sha256"],
        "token_usage": {"prompt_tokens_estimate": 1, "completion_tokens_estimate": 1},
        "retries": trace["retries"],
        "retry_errors": trace["retry_errors"],
        "updated_at": "2030-01-10T08:59:01+09:00",
    }
    write_json(
        tmp_path / "runs" / "checkpoints" / "llm" / "LLMCKPT-linked.json",
        checkpoint,
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "TRACE-daily.json: trace checkpoint token_usage mismatch" in result["findings"]


def test_provenance_audit_checks_session_pack_manifest_integrity(
    tmp_path: Path,
) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    pack_files = (
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    )
    for file_name in pack_files:
        (pack_dir / file_name).write_text(
            f"{file_name} reproducible content\n" * 3,
            encoding="utf-8",
        )
    omission_report = pack_dir / "omission_report.md"
    omission_report.write_text("Future-unavailable episodes are listed.\n", encoding="utf-8")
    brain_file = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "brain_context"
        / "SESSION-test"
        / "brain"
        / "00_world_model.md"
    )
    brain_file.parent.mkdir(parents=True)
    brain_file.write_text("as-of brain context\n", encoding="utf-8")
    brain_ref = brain_file.relative_to(tmp_path).as_posix()
    pack_hashes = {file_name: file_sha256(pack_dir / file_name) for file_name in pack_files}
    token_counts = {
        file_name: max(1, len((pack_dir / file_name).read_text(encoding="utf-8")) // 4)
        for file_name in pack_files
    }
    token_count_total = sum(token_counts.values())
    manifest = {
        "schema_version": "nslab.session_pack_manifest.v1",
        "blocked": True,
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "as_of": "2030-01-10T08:59:59+09:00",
        "mode": "brain",
        "brain_version": "brain-asof-test",
        "brain_files": [brain_ref],
        "brain_file_hashes": {brain_ref: file_sha256(brain_file)},
        "shard_brain_files": [],
        "shard_brain_file_hashes": {},
        "shard_brain_count": 0,
        "accepted_episode_count": 1,
        "available_episode_count": 0,
        "available_episode_ids": [],
        "included_episode_count": 0,
        "included_episode_ids": [],
        "unavailable_episode_count": 1,
        "unavailable_episode_ids": ["EP-future"],
        "budget_omitted_episode_count": 0,
        "budget_omitted_episode_ids": [],
        "omitted_episode_ids": ["EP-future"],
        "available_coverage_complete": True,
        "omission_report_file": "omission_report.md",
        "omission_report_sha256": file_sha256(omission_report),
        "token_budget": 10,
        "token_counts": token_counts,
        "token_count_total": token_count_total,
        "pack_files": list(pack_files),
        "pack_file_count": len(pack_files),
        "pack_file_hashes": pack_hashes,
        "pack_sha256": sha256_text(
            "\n".join(pack_hashes[file_name] for file_name in pack_files)
        ),
        "truncations": [
            {
                "artifact": "memory_cases.md",
                "reason": "episode_available_from_after_cutoff",
                "omitted_episode_ids": ["EP-future"],
            },
            {
                "artifact": "session_pack",
                "reason": "session_pack_required_context_exceeds_token_budget",
                "token_budget": 10,
                "token_count_total": token_count_total,
            },
        ],
        "errors": [
            "session pack excluded future-unavailable episodes",
            "session pack required context exceeds token budget",
        ],
    }
    write_json(pack_dir / "manifest.json", manifest)

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]
    assert result["checked_session_pack_manifests"] == 1

    write_json(
        pack_dir / "manifest.json",
        {
            **manifest,
            "blocked": False,
            "pack_sha256": "bad",
            "shard_brain_count": 1,
            "token_count_total": 1,
            "truncations": [],
            "errors": [],
        },
    )

    failed = audit_provenance(tmp_path)

    assert not failed["passed"]
    label = "session_packs/2030-01-10/manifest.json"
    assert f"{label}: session pack pack_sha256 mismatch" in failed["findings"]
    assert f"{label}: session pack shard_brain_count mismatch" in failed["findings"]
    assert f"{label}: session pack token_count_total mismatch" in failed["findings"]
    assert (
        f"{label}: session pack token budget exceeded without blocked"
        in failed["findings"]
    )
    assert (
        f"{label}: session pack missing required context over budget error"
        in failed["findings"]
    )
    assert (
        f"{label}: session pack missing required context over budget truncation"
        in failed["findings"]
    )
    assert (
        f"{label}: session pack missing future-unavailable episode error"
        in failed["findings"]
    )
    assert (
        f"{label}: session pack missing future-unavailable episode truncation"
        in failed["findings"]
    )


def test_provenance_audit_flags_training_export_manifest_mismatch(tmp_path: Path) -> None:
    export_dir = tmp_path / "training_exports" / "sft"
    export_dir.mkdir(parents=True)
    output_path = export_dir / "sft.jsonl"
    rows = [
        {
            "schema_version": "nslab.training_example.v1",
            "task": "blind_reasoning",
            "training_category": "blind_reasoning_examples",
            "example_id": "TRN-stale",
            "split": "preference",
            "episode_id": "EP-training",
            "hindsight_safe_for_blind_sft": True,
            "source_phase": "POSTMORTEM",
            "input": {},
            "output": {},
        },
        {
            "schema_version": "nslab.training_example.v1",
            "task": "theme_formation",
            "training_category": "theme_formation_examples",
            "episode_id": "EP-training",
            "hindsight_safe_for_blind_sft": False,
            "source_phase": "POSTMORTEM",
            "input": {},
            "output": {},
        },
    ]
    output_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    write_json(
        export_dir / "manifest.json",
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": "sft",
            "output_file": output_path.relative_to(tmp_path).as_posix(),
            "output_sha256": "bad",
            "row_count": 99,
            "task_counts": {},
            "required_training_categories": ["bad"],
            "training_categories": ["bad"],
            "category_counts": {},
            "missing_training_categories": ["bad"],
            "blind_safe_row_count": 0,
            "hindsight_row_count": 0,
            "source_phase_counts": {},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert result["checked_training_export_manifests"] == 1
    findings = result["findings"]
    label = "training_exports/sft/manifest.json"
    assert f"{label}: training export required_training_categories mismatch" in findings
    assert f"{label}: training export training_categories mismatch" in findings
    assert f"{label}: training export source_hashes invalid" in findings
    assert f"{label}: training export output_sha256 mismatch" in findings
    assert f"{label}: training export row 1 split mismatch" in findings
    assert f"{label}: training export row 1 example_id mismatch" in findings
    assert f"{label}: training export row 1 provenance missing" in findings
    assert f"{label}: training export row 1 source_phase mismatch" in findings
    assert f"{label}: training export row 2 split invalid" in findings
    assert f"{label}: training export row 2 example_id invalid" in findings
    assert f"{label}: training export row 2 provenance missing" in findings
    assert f"{label}: training export row 2 mixes postmortem into blind SFT" in findings
    assert f"{label}: training export row_count mismatch" in findings
    assert f"{label}: training export category_counts mismatch" in findings
    assert f"{label}: training export source_phase_counts mismatch" in findings
    assert f"{label}: training export blind_safe_row_count mismatch" in findings
    assert f"{label}: training export hindsight_row_count mismatch" in findings
    assert f"{label}: training export phase_outputs invalid" in findings


def test_provenance_audit_verifies_training_export_source_hashes(
    tmp_path: Path,
) -> None:
    export_dir = tmp_path / "training_exports" / "sft"
    export_dir.mkdir(parents=True)
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True)
    write_json(
        accepted_dir / "EP-training.json",
        {
            "episode_id": "EP-training",
            "source": "accepted episode body",
        },
    )
    output_path = export_dir / "sft.jsonl"
    fake_hash = "0" * 64
    row = {
        "schema_version": "nslab.training_example.v1",
        "task": "blind_reasoning",
        "training_category": "blind_reasoning_examples",
        "episode_id": "EP-training",
        "hindsight_safe_for_blind_sft": True,
        "source_phase": "BLIND",
        "input": {},
        "output": {},
        "provenance": [
            {
                "source_id": "EP-training:accepted_episode",
                "source_type": "accepted_research_episode",
                "uri": "research/accepted/EP-training.json",
                "content_sha256": fake_hash,
            }
        ],
    }
    output_path.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    write_json(
        export_dir / "manifest.json",
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": "sft",
            "output_file": output_path.relative_to(tmp_path).as_posix(),
            "output_sha256": file_sha256(output_path),
            "row_count": 1,
            "task_counts": {"blind_reasoning": 1},
            "required_training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "positive_vs_negative_candidate_preferences",
                "failure_correction_examples",
            ],
            "training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "category_counts": {
                "blind_reasoning_examples": 1,
                "theme_formation_examples": 0,
                "beneficiary_discovery_examples": 0,
                "leader_selection_comparisons": 0,
                "failure_correction_examples": 0,
            },
            "missing_training_categories": [
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "blind_safe_row_count": 1,
            "hindsight_row_count": 0,
            "source_phase_counts": {"BLIND": 1},
            "source_hashes": {"EP-training": fake_hash},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    label = "training_exports/sft/manifest.json"
    assert f"{label}: training export source_hash mismatch: EP-training" in result["findings"]


def test_provenance_audit_flags_absolute_training_export_output_file(
    tmp_path: Path,
) -> None:
    export_dir = tmp_path / "training_exports" / "sft"
    export_dir.mkdir(parents=True)
    output_path = export_dir / "sft.jsonl"
    output_path.write_text("", encoding="utf-8")
    write_json(
        export_dir / "manifest.json",
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": "sft",
            "output_file": output_path.as_posix(),
            "output_sha256": file_sha256(output_path),
            "row_count": 0,
            "episode_count": 0,
            "episode_ids": [],
            "eligible_episode_count": 0,
            "skipped_episode_count": 0,
            "skipped_episodes": [],
            "source_hashes": {},
            "task_counts": {},
            "required_training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "positive_vs_negative_candidate_preferences",
                "failure_correction_examples",
            ],
            "training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "category_counts": {
                "blind_reasoning_examples": 0,
                "theme_formation_examples": 0,
                "beneficiary_discovery_examples": 0,
                "leader_selection_comparisons": 0,
                "failure_correction_examples": 0,
            },
            "missing_training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "blind_safe_row_count": 0,
            "hindsight_row_count": 0,
            "source_phase_counts": {},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    label = "training_exports/sft/manifest.json"
    assert (
        f"{label}: training export output_file must be project-relative"
        in result["findings"]
    )


def test_provenance_audit_flags_blind_safe_training_row_hindsight_content(
    tmp_path: Path,
) -> None:
    export_dir = tmp_path / "training_exports" / "sft"
    export_dir.mkdir(parents=True)
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True)
    accepted_path = accepted_dir / "EP-training.json"
    write_json(
        accepted_path,
        {
            "episode_id": "EP-training",
            "postmortem": {
                "summary": "Winner hit and loser failed.",
                "failure_codes": ["DIRECTNESS_ERROR"],
                "lessons": ["prefer verified directness over loose theme breadth"],
            },
        },
    )
    source_hash = file_sha256(accepted_path)
    output_path = export_dir / "sft.jsonl"
    row = {
        "schema_version": "nslab.training_example.v1",
        "task": "blind_reasoning",
        "training_category": "blind_reasoning_examples",
        "episode_id": "EP-training",
        "hindsight_safe_for_blind_sft": True,
        "source_phase": "BLIND",
        "input": {"current_news": ["pre-cutoff event"]},
        "output": {
            "summary": "Blind answer contaminated by prefer verified directness over loose theme breadth"
        },
        "provenance": [
            {
                "source_id": "EP-training:accepted_episode",
                "source_type": "accepted_research_episode",
                "uri": "research/accepted/EP-training.json",
                "content_sha256": source_hash,
            }
        ],
    }
    output_path.write_text(
        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_json(
        export_dir / "manifest.json",
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": "sft",
            "output_file": output_path.relative_to(tmp_path).as_posix(),
            "output_sha256": file_sha256(output_path),
            "row_count": 1,
            "episode_count": 1,
            "episode_ids": ["EP-training"],
            "eligible_episode_count": 1,
            "skipped_episode_count": 0,
            "skipped_episodes": [],
            "task_counts": {"blind_reasoning": 1},
            "required_training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "positive_vs_negative_candidate_preferences",
                "failure_correction_examples",
            ],
            "training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "category_counts": {
                "blind_reasoning_examples": 1,
                "theme_formation_examples": 0,
                "beneficiary_discovery_examples": 0,
                "leader_selection_comparisons": 0,
                "failure_correction_examples": 0,
            },
            "missing_training_categories": [
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "blind_safe_row_count": 1,
            "hindsight_row_count": 0,
            "source_phase_counts": {"BLIND": 1},
            "source_hashes": {"EP-training": source_hash},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    label = "training_exports/sft/manifest.json"
    assert (
        f"{label}: training export row 1 blind-safe SFT contains postmortem content"
        in result["findings"]
    )


def test_provenance_audit_flags_training_export_episode_coverage_gap(
    tmp_path: Path,
) -> None:
    export_dir = tmp_path / "training_exports" / "sft"
    export_dir.mkdir(parents=True)
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True)
    write_json(accepted_dir / "EP-included.json", {"episode_id": "EP-included"})
    write_json(accepted_dir / "EP-omitted.json", {"episode_id": "EP-omitted"})
    source_hashes = {
        "EP-included": file_sha256(accepted_dir / "EP-included.json"),
        "EP-omitted": file_sha256(accepted_dir / "EP-omitted.json"),
    }
    output_path = export_dir / "sft.jsonl"
    row = {
        "schema_version": "nslab.training_example.v1",
        "task": "blind_reasoning",
        "training_category": "blind_reasoning_examples",
        "episode_id": "EP-included",
        "hindsight_safe_for_blind_sft": True,
        "source_phase": "BLIND",
        "input": {},
        "output": {},
        "provenance": [
            {
                "source_id": "EP-included:accepted_episode",
                "source_type": "accepted_research_episode",
                "uri": "research/accepted/EP-included.json",
                "content_sha256": source_hashes["EP-included"],
            }
        ],
    }
    output_path.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    write_json(
        export_dir / "manifest.json",
        {
            "schema_version": "nslab.training_export_manifest.v1",
            "kind": "sft",
            "output_file": output_path.relative_to(tmp_path).as_posix(),
            "output_sha256": file_sha256(output_path),
            "row_count": 1,
            "episode_count": 2,
            "episode_ids": ["EP-included", "EP-omitted"],
            "eligible_episode_count": 2,
            "skipped_episode_count": 0,
            "skipped_episodes": [],
            "source_hashes": source_hashes,
            "task_counts": {"blind_reasoning": 1},
            "required_training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "positive_vs_negative_candidate_preferences",
                "failure_correction_examples",
            ],
            "training_categories": [
                "blind_reasoning_examples",
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "category_counts": {
                "blind_reasoning_examples": 1,
                "theme_formation_examples": 0,
                "beneficiary_discovery_examples": 0,
                "leader_selection_comparisons": 0,
                "failure_correction_examples": 0,
            },
            "missing_training_categories": [
                "theme_formation_examples",
                "beneficiary_discovery_examples",
                "leader_selection_comparisons",
                "failure_correction_examples",
            ],
            "blind_safe_row_count": 1,
            "hindsight_row_count": 0,
            "source_phase_counts": {"BLIND": 1},
        },
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    label = "training_exports/sft/manifest.json"
    assert f"{label}: training export episode coverage mismatch" in result["findings"]


def test_provenance_audit_checks_analysis_bundle_integrity(tmp_path: Path) -> None:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir()
    (reports_dir / "20300110_nslab_episode_bundle.md").write_text(
        "<!-- NSLAB:BEGIN research_report.md -->\n"
        "# incomplete bundle\n"
        "<!-- NSLAB:END research_report.md -->\n",
        encoding="utf-8",
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert result["checked_analysis_bundles"] == 1
    assert any(
        finding.startswith(
            "reports/20300110_nslab_episode_bundle.md: analysis bundle invalid:"
        )
        for finding in result["findings"]
    )


def test_provenance_audit_accepts_red_team_artifact_links(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "prediction_id": "PRED-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "red_team_candidate_review": "red-team-hash",
                "final_synthesis": "final-hash",
            },
            "token_counts": {"final_synthesis_prompt": 10},
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "red_team_artifacts": ["runs/checkpoints/red_team/RUN-linked.json"],
            "red_team_summary": {
                "candidate_count": 1,
                "required_attack_checks": ["novelty_not_recycled"],
                "required_attack_check_count": 1,
                "finding_count": 1,
                "all_findings_passed_to_synthesis": True,
            },
        },
    )
    write_json(
        tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json",
        {
            "schema_version": "nslab.red_team_artifact.v1",
            "run_id": "RUN-linked",
            "source_prediction_id": "PRED-linked",
            "prompt_version": "red_team.candidate_attack.v2",
            "prompt_sha256": "red-team-hash",
            "created_at": "2030-01-10T08:59:59+09:00",
            "candidate_count": 1,
            "required_attack_checks": ["novelty_not_recycled"],
            "candidate_findings": [
                {
                    "candidate_rank": 1,
                    "passed_to_synthesis": True,
                    "attack_checks": [
                        {
                            "name": "novelty_not_recycled",
                            "status": "needs_synthesis_review",
                            "passed_to_synthesis": True,
                        }
                    ],
                }
            ],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]


def test_provenance_audit_flags_red_team_artifact_mismatch(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "prediction_id": "PRED-linked",
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "red_team_candidate_review": "red-team-hash",
                "final_synthesis": "final-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "red_team_artifacts": ["runs/checkpoints/red_team/RUN-linked.json"],
        },
    )
    write_json(
        tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json",
        {
            "schema_version": "nslab.red_team_artifact.v1",
            "run_id": "RUN-other",
            "source_prediction_id": "",
            "prompt_version": "red_team.candidate_attack.v2",
            "prompt_sha256": "wrong-hash",
            "created_at": "2030-01-10T08:59:59+09:00",
            "candidate_count": 0,
            "candidate_findings": [],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert (
        "2030-01-10.json: red-team artifact run_id mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact missing source_prediction_id"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact prompt hash mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact candidate_count mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]
    assert (
        "2030-01-10.json: red-team artifact required_attack_checks is invalid: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in result["findings"]


def test_provenance_audit_validates_red_team_summary_contract(tmp_path: Path) -> None:
    (tmp_path / "predictions").mkdir()
    (tmp_path / "reports").mkdir()
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        {
            "prediction_id": "PRED-linked",
            "blind_artifact_sha256": "abc123",
            "context_manifest_id": "RUN-linked",
            "blind_analysis": _blind_analysis_with_provenance(),
            "candidates": [_candidate_with_provenance()],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-linked.json",
        {
            "run_id": "RUN-linked",
            "prompt_hashes": {
                "blind_analysis": "blind-hash",
                "red_team_candidate_review": "red-team-hash",
                "final_synthesis": "final-hash",
            },
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "brain_file_hashes": {"brain/current/brain_manifest.json": "789"},
            "red_team_artifacts": ["runs/checkpoints/red_team/RUN-linked.json"],
            "red_team_summary": {
                "candidate_count": 2,
                "required_attack_checks": ["wrong_check"],
                "required_attack_check_count": 2,
                "finding_count": 2,
                "all_findings_passed_to_synthesis": True,
            },
        },
    )
    write_json(
        tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json",
        {
            "schema_version": "nslab.red_team_artifact.v1",
            "run_id": "RUN-linked",
            "source_prediction_id": "PRED-linked",
            "prompt_version": "red_team.candidate_attack.v2",
            "prompt_sha256": "red-team-hash",
            "created_at": "2030-01-10T08:59:59+09:00",
            "candidate_count": 1,
            "required_attack_checks": ["novelty_not_recycled"],
            "candidate_findings": [
                {
                    "candidate_rank": 1,
                    "passed_to_synthesis": False,
                    "attack_checks": [
                        {
                            "name": "novelty_not_recycled",
                            "status": "needs_synthesis_review",
                            "passed_to_synthesis": True,
                        }
                    ],
                }
            ],
        },
    )
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert (
        "2030-01-10.json: red-team artifact summary candidate_count mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in findings
    assert (
        "2030-01-10.json: red-team artifact summary finding_count mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in findings
    assert (
        "2030-01-10.json: red-team artifact summary required_attack_checks mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in findings
    assert (
        "2030-01-10.json: red-team artifact summary "
        "all_findings_passed_to_synthesis mismatch: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in findings
    assert (
        "2030-01-10.json: red-team artifact finding 1 not passed to synthesis: "
        "runs/checkpoints/red_team/RUN-linked.json"
    ) in findings


def test_lookahead_audit_flags_future_retrieved_episode_and_context_file(
    tmp_path: Path,
) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "research" / "accepted").mkdir(parents=True)
    (tmp_path / "brain" / "current").mkdir(parents=True)
    (tmp_path / "memory" / "shard_brains" / "current").mkdir(parents=True)
    (tmp_path / "runs" / "checkpoints" / "memory_sweep" / "RUN-lookahead").mkdir(
        parents=True
    )
    write_json(
        tmp_path / "research" / "accepted" / "EP-future.json",
        {
            "episode_id": "EP-future",
            "available_from": "2030-01-10T09:30:00+09:00",
        },
    )
    (tmp_path / "brain" / "current" / "00_world_model.md").write_text(
        "future context leak EP-future",
        encoding="utf-8",
    )
    (tmp_path / "memory" / "shard_brains" / "current" / "shard_0001.md").write_text(
        "future shard leak EP-future",
        encoding="utf-8",
    )
    write_json(
        tmp_path
        / "runs"
        / "checkpoints"
        / "memory_sweep"
        / "RUN-lookahead"
        / "shard_0001.json",
        {"episode_ids": ["EP-future"], "related_lessons": ["future leak EP-future"]},
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-lookahead.json",
        {
            "run_id": "RUN-lookahead",
            "mode": "brain",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "retrieved_episode_ids": ["EP-future"],
            "excluded_retrieved_episode_ids": [],
            "brain_files": ["brain/current/00_world_model.md"],
            "shard_brain_files": ["memory/shard_brains/current/shard_0001.md"],
            "memory_sweep_artifacts": [
                "runs/checkpoints/memory_sweep/RUN-lookahead/shard_0001.json"
            ],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-lookahead.json: future retrieved episode not excluded: EP-future" in findings
    assert (
        "RUN-lookahead.json: context file contains future episode EP-future: "
        "brain/current/00_world_model.md"
    ) in findings
    assert (
        "RUN-lookahead.json: context file contains future episode EP-future: "
        "memory/shard_brains/current/shard_0001.md"
    ) in findings
    assert (
        "RUN-lookahead.json: context file contains future episode EP-future: "
        "runs/checkpoints/memory_sweep/RUN-lookahead/shard_0001.json"
    ) in findings


def test_lookahead_audit_flags_missing_manifest_time_fields(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-missing-time.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-missing-time",
            "mode": "brain",
            "price_snapshot": {"allowed_through": "2030-01-09"},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert "RUN-missing-time.json: missing trade_date" in result["findings"]
    assert "RUN-missing-time.json: missing cutoff_at" in result["findings"]
    assert "RUN-missing-time.json: missing as_of" in result["findings"]

    with_requested_date = audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))
    assert not with_requested_date["passed"]
    assert "RUN-missing-time.json: missing trade_date" in with_requested_date["findings"]


def test_lookahead_audit_flags_manifest_trade_date_request_mismatch(
    tmp_path: Path,
) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-date-mismatch.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-date-mismatch",
            "mode": "brain",
            "trade_date": "2030-01-11",
            "cutoff_at": "2030-01-11T08:59:59+09:00",
            "as_of": "2030-01-11T08:59:59+09:00",
            "accepted_episode_count": 0,
            "total_accepted_episode_count": 0,
            "available_episode_count": 0,
            "unavailable_episode_count": 0,
            "unavailable_episode_ids": [],
            "swept_episode_count": 0,
            "swept_episode_ids": [],
            "price_snapshot": {
                "allowed_through": "2030-01-10",
                "as_of": "2030-01-11T08:59:59+09:00",
            },
        },
    )

    clean = audit_lookahead(tmp_path)
    mismatch = audit_lookahead(tmp_path, trade_date=date(2030, 1, 10))

    assert clean["passed"], clean["findings"]
    assert not mismatch["passed"]
    assert (
        "RUN-date-mismatch.json: trade_date does not match requested audit date"
        in mismatch["findings"]
    )


def test_lookahead_audit_flags_manifest_as_of_after_cutoff(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-as-of-leak.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-as-of-leak",
            "mode": "brain",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T09:00:00+09:00",
            "price_snapshot": {"allowed_through": "2030-01-09"},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert "RUN-as-of-leak.json: as_of is after cutoff_at" in result["findings"]


def test_lookahead_audit_validates_price_snapshot_contract(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    base_manifest = {
        "schema_version": "nslab.context_manifest.v1",
        "mode": "brain",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "as_of": "2030-01-10T08:59:59+09:00",
        "accepted_episode_count": 0,
        "total_accepted_episode_count": 0,
        "available_episode_count": 0,
        "unavailable_episode_count": 0,
        "unavailable_episode_ids": [],
        "swept_episode_count": 0,
        "swept_episode_ids": [],
    }
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-missing-price.json",
        {**base_manifest, "run_id": "RUN-missing-price"},
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-invalid-price-date.json",
        {
            **base_manifest,
            "run_id": "RUN-invalid-price-date",
            "price_snapshot": {"allowed_through": "2030/01/09"},
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-future-price-snapshot.json",
        {
            **base_manifest,
            "run_id": "RUN-future-price-snapshot",
            "price_snapshot": {
                "allowed_through": "2030-01-09",
                "as_of": "2030-01-10T09:00:00+09:00",
            },
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "RUN-missing-price.json: missing price_snapshot" in findings
    assert (
        "RUN-invalid-price-date.json: price allowed_through invalid" in findings
    )
    assert (
        "RUN-future-price-snapshot.json: price snapshot as_of is after cutoff_at"
        in findings
    )


def test_lookahead_audit_validates_context_manifest_episode_scope(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    (tmp_path / "research" / "accepted").mkdir(parents=True)
    available_episode = ResearchEpisode(
        episode_id="EP-available",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Available lesson."),
        available_from=datetime(2030, 1, 10, 8, 30, 0, tzinfo=KST),
    )
    future_episode = ResearchEpisode(
        episode_id="EP-future",
        trade_date=date(2030, 1, 9),
        cutoff_at=datetime(2030, 1, 9, 8, 59, 59, tzinfo=KST),
        created_at=datetime(2030, 1, 9, 16, 0, 0, tzinfo=KST),
        research_version="test",
        price_source_snapshot={"source": "test"},
        blind_analysis=BlindAnalysis(summary="Future lesson."),
        available_from=datetime(2030, 1, 10, 9, 30, 0, tzinfo=KST),
    )
    write_json(
        tmp_path / "research" / "accepted" / "EP-available.json",
        available_episode.model_dump(mode="json"),
    )
    write_json(
        tmp_path / "research" / "accepted" / "EP-future.json",
        future_episode.model_dump(mode="json"),
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-episode-scope.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-episode-scope",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 2,
            "total_accepted_episode_count": 3,
            "available_episode_count": 1,
            "unavailable_episode_count": 0,
            "unavailable_episode_ids": ["EP-available"],
            "swept_episode_count": 1,
            "swept_episode_ids": ["EP-available", "EP-future"],
            "price_snapshot": {"allowed_through": "2030-01-09"},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert (
        "RUN-episode-scope.json: episode scope accepted_episode_count_mismatch"
        in findings
    )
    assert (
        "RUN-episode-scope.json: episode scope total_accepted_episode_count_mismatch"
        in findings
    )
    assert (
        "RUN-episode-scope.json: episode scope available_episode_count_mismatch"
        in findings
    )
    assert (
        "RUN-episode-scope.json: episode scope unavailable_episode_count_mismatch"
        in findings
    )
    assert (
        "RUN-episode-scope.json: episode scope unavailable_episode_ids_mismatch"
        in findings
    )
    assert (
        "RUN-episode-scope.json: episode scope swept_episode_count_mismatch"
        in findings
    )
    assert "RUN-episode-scope.json: future swept episode: EP-future" in findings


def test_lookahead_audit_flags_news_only_blind_protocol_violations(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-news-only-violation.json",
        {
            "run_id": "RUN-news-only-violation",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "NEWS_ONLY_STRICT",
            "blind_web_search_call_count": 1,
            "blind_price_repository_access_count": 1,
            "blind_current_price_access_count": 1,
            "no_d_outcome_exposed": False,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert (
        "RUN-news-only-violation.json: blind_web_search_call_count must be 0 in NEWS_ONLY_STRICT"
    ) in findings
    assert (
        "RUN-news-only-violation.json: blind_price_repository_access_count must be 0 in NEWS_ONLY_STRICT"
    ) in findings
    assert (
        "RUN-news-only-violation.json: blind_current_price_access_count must be 0 in NEWS_ONLY_STRICT"
    ) in findings
    assert "RUN-news-only-violation.json: no_d_outcome_exposed must be true" in findings


def test_lookahead_audit_allows_d_minus_one_price_repository_access(
    tmp_path: Path,
) -> None:
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    base_manifest = {
        "run_id": "RUN-d-minus-one-price",
        "mode": "exhaustive",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "blind_context_mode": "D_MINUS_ONE_PRICE_BLIND",
        "blind_web_search_call_count": 0,
        "blind_price_repository_access_count": 3,
        "blind_current_price_access_count": 0,
        "no_d_outcome_exposed": True,
        "accepted_episode_count": 0,
        "swept_episode_count": 0,
        "price_snapshot": {
            "source_name": "test-price",
            "allowed_through": "2030-01-09",
            "as_of": "2030-01-10T08:59:59+09:00",
        },
    }
    write_json(manifest_dir / "RUN-d-minus-one-price.json", base_manifest)

    clean = audit_lookahead(tmp_path)

    assert clean["passed"], clean["findings"]

    write_json(
        manifest_dir / "RUN-d-minus-one-price.json",
        {**base_manifest, "blind_current_price_access_count": 1},
    )
    current_price_access = audit_lookahead(tmp_path)

    assert not current_price_access["passed"]
    assert (
        "RUN-d-minus-one-price.json: blind_current_price_access_count must be 0 "
        "in D_MINUS_ONE_PRICE_BLIND"
    ) in current_price_access["findings"]


def test_lookahead_audit_flags_cutoff_safe_web_blind_artifact_leaks(
    tmp_path: Path,
) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    source_dir = tmp_path / "runs" / "checkpoints" / "source_ledger" / "RUN-web"
    web_dir = tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-web"
    source_dir.mkdir(parents=True)
    web_dir.mkdir(parents=True)
    web_artifact = web_dir / "web_sources.jsonl"
    web_payload = (
        canonical_json(
            {
                "schema_version": "nslab.web_source.v1",
                "source_id": "WEB-FUTURE",
                "query": "future leak",
                "title": "future source",
                "url": "https://example.test/future",
                "source_url": "https://example.test/future",
                "snippet": "future",
                "published_at": "2030-01-10T09:30:00+09:00",
                "timestamp_precision": "unknown_precision",
                "retrieved_at": "2030-01-10T09:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "time_verified": False,
                "available_before_cutoff": False,
                "content_sha256": "abc",
                "opened_text_sha256": "def",
            }
        )
        + "\n"
    )
    web_artifact.write_text(web_payload, encoding="utf-8")
    source_artifact = source_dir / "source_ledger.jsonl"
    source_artifact.write_text("", encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-web.json",
        {
            "run_id": "RUN-web",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "CUTOFF_SAFE_WEB_BLIND",
            "blind_web_search_call_count": 1,
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "source_ledger_artifact": source_artifact.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": sha256_text(""),
            "source_ledger_entry_count": 0,
            "web_sources": ["WEB-FUTURE"],
            "excluded_web_source_ids": ["WEB-FUTURE"],
            "web_source_artifact": web_artifact.relative_to(tmp_path).as_posix(),
            "web_source_sha256": sha256_text(web_payload),
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-web.json: web source is both included and excluded" in findings
    assert "RUN-web.json: web_source:1 is not cutoff verified" in findings
    assert "RUN-web.json: web_source:1 invalid timestamp_precision" in findings
    assert "RUN-web.json: web_source:1 after cutoff" in findings


def test_lookahead_audit_checks_excluded_web_source_artifact(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    web_dir = tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-web"
    web_dir.mkdir(parents=True)
    excluded_artifact = web_dir / "excluded_web_sources.jsonl"
    excluded_payload = (
        canonical_json(
            {
                "schema_version": "nslab.excluded_web_source.v1",
                "source_id": "WEB-EXCLUDED",
                "query": "verification query",
                "title": "excluded source",
                "url": "https://example.test/excluded",
                "source_url": "https://example.test/excluded",
                "snippet": "excluded",
                "published_at": "2030-01-10T08:30:00+09:00",
                "timestamp_precision": "date_only_end_of_day",
                "retrieved_at": "2030-01-10T08:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "exclusion_reason": "timestamp_verification_failed",
                "time_verified": True,
                "available_before_cutoff": True,
                "content_sha256": "abc",
            }
        )
        + "\n"
    )
    excluded_artifact.write_text(excluded_payload, encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-web.json",
        {
            "run_id": "RUN-web",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "CUTOFF_SAFE_WEB_BLIND",
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "web_sources": [],
            "excluded_web_source_ids": ["WEB-OTHER"],
            "excluded_web_source_artifact": excluded_artifact.relative_to(tmp_path).as_posix(),
            "excluded_web_source_sha256": "bad",
            "excluded_web_source_count": 2,
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "RUN-web.json: excluded_web_source_sha256 mismatch" in findings
    assert "RUN-web.json: excluded_web_source:1 is cutoff verified" in findings
    assert (
        "RUN-web.json: excluded_web_source:1 date_only_end_of_day must use 23:59:59"
    ) in findings
    assert "RUN-web.json: excluded_web_source_ids do not match excluded artifact" in findings
    assert "RUN-web.json: excluded_web_source_count mismatch" in findings


def test_lookahead_audit_checks_candidate_web_check_artifacts(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    web_dir = tmp_path / "runs" / "checkpoints" / "candidate_web_checks" / "RUN-candidate"
    web_dir.mkdir(parents=True)
    artifact = web_dir / "candidate_web_checks.jsonl"
    payload = (
        canonical_json(
            {
                "schema_version": "nslab.candidate_web_check.v1",
                "run_id": "RUN-candidate",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "candidate_subject_type": "final_candidate",
                "candidate_expansion_path": None,
                "verification_focus": ["listed_security_and_exact_ticker"],
                "source_id": "WEB-CANDIDATE-FUTURE",
                "query": "candidate verification",
                "title": "future source",
                "url": "https://example.test/future",
                "source_url": "https://example.test/future",
                "snippet": "future",
                "published_at": "2030-01-10T09:30:00+09:00",
                "timestamp_precision": "date_only_end_of_day",
                "retrieved_at": "2030-01-10T09:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "time_verified": False,
                "available_before_cutoff": False,
                "opened_text": "raw opened text must not be copied",
                "content": "raw opened page content must not be copied",
            }
        )
        + "\n"
    )
    artifact.write_text(payload, encoding="utf-8")
    excluded_artifact = web_dir / "excluded_candidate_web_checks.jsonl"
    excluded_payload = (
        canonical_json(
            {
                "schema_version": "nslab.excluded_candidate_web_check.v1",
                "run_id": "RUN-candidate",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "candidate_subject_type": "final_candidate",
                "candidate_expansion_path": None,
                "source_id": "WEB-CANDIDATE-EXCLUDED",
                "query": "candidate verification",
                "title": "excluded source",
                "url": "https://example.test/excluded",
                "source_url": "https://example.test/excluded",
                "snippet": "excluded",
                "published_at": "2030-01-10T08:30:00+09:00",
                "timestamp_precision": "relative_age",
                "retrieved_at": "2030-01-10T08:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "exclusion_reason": "timestamp_verification_failed",
                "time_verified": True,
                "available_before_cutoff": True,
            }
        )
        + "\n"
    )
    excluded_artifact.write_text(excluded_payload, encoding="utf-8")
    verification_artifact = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "candidate_verifications"
        / "RUN-candidate"
        / "candidate_verification.json"
    )
    verification_artifact.parent.mkdir(parents=True)
    write_json(
        verification_artifact,
        {
            "schema_version": "nslab.candidate_verification.v1",
            "run_id": "RUN-candidate",
            "created_at": "2030-01-10T08:58:00+09:00",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "required_dimensions": ["listed_security_and_exact_ticker"],
            "subject_count": 1,
            "findings": [
                {
                    "subject_type": "final_candidate",
                    "candidate_rank": 1,
                    "candidate_ticker": "UNKNOWN",
                    "candidate_company_name": "CandidateCo",
                    "candidate_path_type": "SINGLE_EVENT",
                    "query": "candidate verification",
                    "source_count": 1,
                    "excluded_source_count": 1,
                    "accepted_source_ids": ["WEB-CANDIDATE-NOT-MANIFEST"],
                    "excluded_source_ids": ["WEB-CANDIDATE-EXCLUDED-NOT-MANIFEST"],
                    "verification_dimensions": [],
                    "d_minus_one_market_data_only": False,
                    "uncertainties": [],
                }
            ],
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-candidate.json",
        {
            "run_id": "RUN-candidate",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "CUTOFF_SAFE_WEB_BLIND",
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "candidate_web_source_ids": ["WEB-CANDIDATE-OTHER"],
            "candidate_web_check_artifact": artifact.relative_to(tmp_path).as_posix(),
            "candidate_web_check_sha256": sha256_text(payload),
            "candidate_web_check_count": 2,
            "candidate_web_check_summary": {
                "source_count": 2,
                "excluded_source_count": 2,
                "subject_count": 2,
                "final_candidate_subject_count": 2,
                "candidate_expansion_subject_count": 1,
                "expansion_paths": ["CONTINUATION"],
                "verification_focus": ["listed_security_and_exact_ticker"],
            },
            "excluded_candidate_web_source_ids": ["WEB-CANDIDATE-OTHER-EXCLUDED"],
            "excluded_candidate_web_check_artifact": excluded_artifact.relative_to(
                tmp_path
            ).as_posix(),
            "excluded_candidate_web_check_sha256": "bad",
            "excluded_candidate_web_check_count": 2,
            "candidate_verification_artifact": verification_artifact.relative_to(
                tmp_path
            ).as_posix(),
            "candidate_verification_sha256": "bad-verification",
            "candidate_verification_count": 2,
            "candidate_verification_summary": {
                "required_dimensions": ["listed_security_and_exact_ticker"],
                "finding_count": 2,
                "subject_count": 2,
                "status_counts": {"source_collected": 1},
                "subjects_without_cutoff_safe_sources": 1,
                "candidate_expansion_subject_count": 1,
                "d_minus_one_only_subject_count": 1,
            },
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "RUN-candidate.json: candidate_web_check:1 is not cutoff verified" in findings
    assert (
        "RUN-candidate.json: candidate_web_check:1 "
        "date_only_end_of_day must use 23:59:59"
    ) in findings
    assert "RUN-candidate.json: candidate_web_check:1 after cutoff" in findings
    assert (
        "RUN-candidate.json: candidate_web_check:1 missing fields: content_sha256"
        in findings
    )
    assert (
        "RUN-candidate.json: candidate_web_check:1 must not duplicate opened_text"
        in findings
    )
    assert (
        "RUN-candidate.json: candidate_web_check:1 must not duplicate body/content"
        in findings
    )
    assert "RUN-candidate.json: candidate_web_source_ids do not match artifact" in findings
    assert "RUN-candidate.json: candidate_web_check_count mismatch" in findings
    assert "RUN-candidate.json: excluded_candidate_web_check_sha256 mismatch" in findings
    assert (
        "RUN-candidate.json: excluded_candidate_web_check:1 invalid timestamp_precision"
    ) in findings
    assert (
        "RUN-candidate.json: excluded_candidate_web_check:1 is cutoff verified"
        in findings
    )
    assert (
        "RUN-candidate.json: excluded_candidate_web_source_ids do not match artifact"
        in findings
    )
    assert "RUN-candidate.json: excluded_candidate_web_check_count mismatch" in findings
    assert "RUN-candidate.json: candidate_web_check source_count mismatch" in findings
    assert (
        "RUN-candidate.json: candidate_web_check excluded_source_count mismatch"
        in findings
    )
    assert "RUN-candidate.json: candidate_web_check subject_count mismatch" in findings
    assert (
        "RUN-candidate.json: candidate_web_check final_candidate_subject_count mismatch"
        in findings
    )
    assert (
        "RUN-candidate.json: candidate_web_check "
        "candidate_expansion_subject_count mismatch"
    ) in findings
    assert "RUN-candidate.json: candidate_web_check expansion_paths mismatch" in findings
    assert "RUN-candidate.json: candidate_verification_sha256 mismatch" in findings
    assert "RUN-candidate.json: candidate_verification_count mismatch" in findings
    assert "RUN-candidate.json: candidate_verification finding_count mismatch" in findings
    assert "RUN-candidate.json: candidate_verification subject_count mismatch" in findings
    assert (
        "RUN-candidate.json: candidate_verification dimension_coverage mismatch"
        in findings
    )
    assert "RUN-candidate.json: candidate_verification status_counts mismatch" in findings
    assert "RUN-candidate.json: candidate_verification source_counts mismatch" in findings
    assert (
        "RUN-candidate.json: candidate_verification "
        "candidate_expansion_subject_count mismatch"
    ) in findings
    assert (
        "RUN-candidate.json: candidate_verification "
        "subjects_without_cutoff_safe_sources mismatch"
    ) in findings
    assert (
        "RUN-candidate.json: candidate_verification d_minus_one_only_subject_count mismatch"
        in findings
    )
    assert (
        "RUN-candidate.json: candidate_verification:1 accepted_source_ids not in "
        "candidate_web_source_ids"
        in findings
    )
    assert (
        "RUN-candidate.json: candidate_verification:1 excluded_source_ids not in "
        "excluded_candidate_web_source_ids"
        in findings
    )
    assert (
        "RUN-candidate.json: candidate_verification:1 verification_dimensions missing"
        in findings
    )


def test_lookahead_audit_checks_final_synthesis_context_sources(
    tmp_path: Path,
) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    web_dir = tmp_path / "runs" / "checkpoints" / "web_sources" / "RUN-final-context"
    candidate_web_dir = (
        tmp_path / "runs" / "checkpoints" / "candidate_web_checks" / "RUN-final-context"
    )
    final_context_dir = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "final_synthesis_context"
        / "RUN-final-context"
    )
    web_dir.mkdir(parents=True)
    candidate_web_dir.mkdir(parents=True)
    final_context_dir.mkdir(parents=True)
    excluded_web_artifact = web_dir / "excluded_web_sources.jsonl"
    excluded_web_text = (
        canonical_json(
            {
                "schema_version": "nslab.excluded_web_source.v1",
                "run_id": "RUN-final-context",
                "source_id": "WEB-FUTURE",
                "query": "future web source",
                "title": "future",
                "url": "https://example.test/future",
                "source_url": "https://example.test/future",
                "snippet": "future",
                "published_at": "2030-01-10T09:30:00+09:00",
                "retrieved_at": "2030-01-10T09:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "exclusion_reason": "published_after_cutoff",
                "time_verified": False,
                "available_before_cutoff": False,
            }
        )
        + "\n"
    )
    excluded_web_artifact.write_text(excluded_web_text, encoding="utf-8")
    excluded_candidate_artifact = candidate_web_dir / "excluded_candidate_web_checks.jsonl"
    excluded_candidate_text = (
        canonical_json(
            {
                "schema_version": "nslab.excluded_candidate_web_check.v1",
                "run_id": "RUN-final-context",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "source_id": "WEB-CANDIDATE-FUTURE",
                "query": "future candidate source",
                "title": "future candidate",
                "url": "https://example.test/candidate-future",
                "source_url": "https://example.test/candidate-future",
                "snippet": "future candidate",
                "published_at": "2030-01-10T09:45:00+09:00",
                "retrieved_at": "2030-01-10T09:46:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "exclusion_reason": "published_after_cutoff",
                "time_verified": False,
                "available_before_cutoff": False,
            }
        )
        + "\n"
    )
    excluded_candidate_artifact.write_text(excluded_candidate_text, encoding="utf-8")
    final_payload = {
        "run_id": "RUN-final-context",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "web_research": {
            "sources": [
                {
                    "source_id": "WEB-FUTURE",
                    "url": "https://example.test/future",
                    "published_at": "2030-01-10T09:30:00+09:00",
                    "time_verified": True,
                },
                {
                    "source_id": "WEB-UNVERIFIED",
                    "url": "https://example.test/unverified",
                    "published_at": "2030-01-10T08:30:00+09:00",
                    "time_verified": False,
                },
            ]
        },
        "candidate_web_checks": [
            {
                "source_id": "WEB-CANDIDATE-FUTURE",
                "source_url": "https://example.test/candidate-future",
                "published_at": "2030-01-10T09:45:00+09:00",
                "time_verified": False,
            }
        ],
    }
    final_context_artifact = final_context_dir / "final_synthesis_context.json"
    write_json(
        final_context_artifact,
        {
            "schema_version": "nslab.final_synthesis_context.v1",
            "run_id": "RUN-final-context",
            "prompt_version": "test",
            "required_inputs": ["web_research", "candidate_web_checks"],
            "payload_sha256": sha256_text(canonical_json(final_payload)),
            "input_summary": {},
            "payload": final_payload,
        },
    )
    final_context_text = final_context_artifact.read_text(encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-final-context.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-final-context",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "blind_context_mode": "CUTOFF_SAFE_WEB_BLIND",
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "excluded_web_source_ids": ["WEB-FUTURE"],
            "excluded_web_source_artifact": excluded_web_artifact.relative_to(
                tmp_path
            ).as_posix(),
            "excluded_web_source_sha256": sha256_text(excluded_web_text),
            "excluded_web_source_count": 1,
            "excluded_candidate_web_source_ids": ["WEB-CANDIDATE-FUTURE"],
            "excluded_candidate_web_check_artifact": (
                excluded_candidate_artifact.relative_to(tmp_path).as_posix()
            ),
            "excluded_candidate_web_check_sha256": sha256_text(excluded_candidate_text),
            "excluded_candidate_web_check_count": 1,
            "final_synthesis_context_artifact": final_context_artifact.relative_to(
                tmp_path
            ).as_posix(),
            "final_synthesis_context_sha256": sha256_text(final_context_text),
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert (
        "RUN-final-context.json: final_synthesis_context references excluded source: "
        "WEB-FUTURE"
        in findings
    )
    assert (
        "RUN-final-context.json: final_synthesis_context source WEB-FUTURE after cutoff"
        in findings
    )
    assert (
        "RUN-final-context.json: final_synthesis_context source WEB-UNVERIFIED "
        "is not cutoff verified"
        in findings
    )
    assert (
        "RUN-final-context.json: final_synthesis_context references excluded source: "
        "WEB-CANDIDATE-FUTURE"
        in findings
    )
    assert (
        "RUN-final-context.json: final_synthesis_context source WEB-CANDIDATE-FUTURE "
        "after cutoff"
        in findings
    )


def test_lookahead_audit_flags_invalid_row_disposition_artifact(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    artifact_dir = tmp_path / "runs" / "checkpoints" / "row_disposition" / "RUN-row"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "row_disposition.jsonl"
    artifact.write_text(
        '{"row_number":1,"title":"raw title must not be duplicated"}\n'
        '{"row_number":1,"disposition":"INCLUDED_BEFORE_CUTOFF"}\n',
        encoding="utf-8",
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-row.json",
        {
            "run_id": "RUN-row",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "row_disposition_artifact": artifact.relative_to(tmp_path).as_posix(),
            "row_disposition_sha256": "wrong",
            "row_disposition_coverage_ratio": 0.5,
            "row_disposition_summary": {"total_rows": 3},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-row.json: row_disposition_sha256 mismatch" in findings
    assert "RUN-row.json: row_disposition:1 must not duplicate title/body" in findings
    assert "RUN-row.json: row_disposition duplicate row_number" in findings
    assert "RUN-row.json: row_disposition total_rows mismatch" in findings
    assert "RUN-row.json: row_disposition coverage ratio must be 1.0" in findings


def test_lookahead_audit_flags_news_window_row_disposition_mismatch(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    artifact_dir = tmp_path / "runs" / "checkpoints" / "row_disposition" / "RUN-window"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "row_disposition.jsonl"
    row = {
        "schema_version": "nslab.row_disposition.v1",
        "run_id": "RUN-window",
        "row_number": 1,
        "event_id": "EVT-window",
        "published_at": "2030-01-09T14:59:00+09:00",
        "collected_at": None,
        "collected_at_present": False,
        "news_window_start_at": "2030-01-09T15:30:00+09:00",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "within_news_window": True,
        "source_id": "SRC-window",
        "disposition": "INCLUDED_IN_NEWS_WINDOW",
        "eligible_for_blind_evidence": True,
    }
    row_text = canonical_json(row) + "\n"
    artifact.write_text(row_text, encoding="utf-8")
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-window.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-window",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "news_window_start_at": "2030-01-09T15:30:00+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "row_disposition_artifact": artifact.relative_to(tmp_path).as_posix(),
            "row_disposition_sha256": sha256_text(row_text),
            "row_disposition_coverage_ratio": 1.0,
            "row_disposition_summary": {"total_rows": 1},
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-window.json: row_disposition:1 within_news_window mismatch" in findings
    assert "RUN-window.json: row_disposition:1 disposition mismatch" in findings
    assert (
        "RUN-window.json: row_disposition:1 eligible_for_blind_evidence mismatch"
        in findings
    )


def test_lookahead_audit_flags_invalid_source_ledger_artifact(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    artifact_dir = tmp_path / "runs" / "checkpoints" / "source_ledger" / "RUN-ledger"
    artifact_dir.mkdir(parents=True)
    artifact = artifact_dir / "source_ledger.jsonl"
    artifact.write_text(
        canonical_json(
            {
                "source_id": "SRC-1",
                "source_type": "news_csv_row",
                "title": "source one",
                "publisher": None,
                "url": "news://one",
                "source_url": "news://mismatch",
                "published_at": "2030-01-10T09:30:00+09:00",
                "timestamp_precision": "date_only_end_of_day",
                "retrieved_at": "2030-01-10T10:00:00+09:00",
                "time_verified": True,
                "available_before_cutoff": False,
                "usage_phase": "BLIND",
                "input_row_ids": [1],
                "content_sha256": "abc",
                "notes": "bad",
                "body": "must not be duplicated",
            }
        )
        + "\n"
        + canonical_json({"source_id": "SRC-1", "usage_phase": "NOPE"})
        + "\n",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-ledger.json",
        {
            "run_id": "RUN-ledger",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "source_ledger_artifact": artifact.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": "wrong",
            "source_ledger_entry_count": 3,
            "source_ledger_summary": {
                "total_sources": 99,
                "blind_sources": 0,
                "outcome_sources": 0,
                "postmortem_sources": 0,
            },
            "web_sources": ["WEB-MISSING"],
            "candidate_web_source_ids": ["WEB-CANDIDATE-MISSING"],
            "excluded_web_source_ids": ["SRC-1"],
            "excluded_candidate_web_source_ids": [],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-ledger.json: source_ledger_sha256 mismatch" in findings
    assert "RUN-ledger.json: source_ledger:1 must not duplicate body/content" in findings
    assert "RUN-ledger.json: source_ledger:1 source_url mismatch" in findings
    assert (
        "RUN-ledger.json: source_ledger:1 date_only_end_of_day must use 23:59:59"
    ) in findings
    assert "RUN-ledger.json: source_ledger:1 BLIND source after cutoff" in findings
    assert "RUN-ledger.json: source_ledger:1 after cutoff" in findings
    assert any(
        finding.startswith("RUN-ledger.json: source_ledger:2 missing fields:")
        for finding in findings
    )
    assert "RUN-ledger.json: source_ledger:2 invalid usage_phase" in findings
    assert "RUN-ledger.json: source_ledger duplicate source_id" in findings
    assert "RUN-ledger.json: source_ledger entry_count mismatch" in findings
    assert "RUN-ledger.json: source_ledger_summary mismatch" in findings
    assert "RUN-ledger.json: source_ledger web_sources mismatch" in findings
    assert (
        "RUN-ledger.json: source_ledger candidate_web_source_ids mismatch"
        in findings
    )
    assert "RUN-ledger.json: source_ledger contains excluded source_id" in findings


def test_lookahead_audit_flags_invalid_blind_seal_artifacts(tmp_path: Path) -> None:
    (tmp_path / "runs" / "manifests").mkdir(parents=True)
    receipt_dir = tmp_path / "runs" / "checkpoints" / "blind_seal" / "RUN-seal"
    phase_dir = tmp_path / "runs" / "checkpoints" / "phase_state" / "RUN-seal"
    receipt_dir.mkdir(parents=True)
    phase_dir.mkdir(parents=True)
    receipt = receipt_dir / "blind_seal_receipt.json"
    phase_state = phase_dir / "phase_state.json"
    write_json(
        receipt,
        {
            "schema_version": "wrong.receipt.v1",
            "run_id": "RUN-other",
            "phase": "OPEN",
            "blind_artifact_sha256": "other",
            "blind_prediction_path": "runs/checkpoints/output_artifacts/RUN-other/blind_prediction.json",
            "row_disposition_sha256": "wrong-row",
            "source_ledger_sha256": "wrong-source",
            "no_d_outcome_exposed": False,
            "validation": {
                "blind_web_search_call_count": 99,
                "blind_price_repository_access_count": 99,
                "blind_current_price_access_count": 1,
                "canonical_blind_hash_verified": False,
            },
        },
    )
    write_json(
        phase_state,
        {
            "schema_version": "wrong.phase_state.v1",
            "run_id": "RUN-other",
            "phase": "OPEN",
            "completed_phases": ["PHASE_A_fast"],
            "blind_seal_receipt_sha256": "wrong",
            "trade_date": "2030-01-11",
            "cutoff_at": "2030-01-10T09:00:00+09:00",
        },
    )
    write_json(
        tmp_path / "runs" / "manifests" / "RUN-seal.json",
        {
            "run_id": "RUN-seal",
            "mode": "exhaustive",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "accepted_episode_count": 0,
            "swept_episode_count": 0,
            "price_snapshot": {"allowed_through": "2030-01-09"},
            "blind_artifact_sha256": "abc",
            "prediction_artifact": "runs/checkpoints/output_artifacts/RUN-seal/blind_prediction.json",
            "row_disposition_sha256": "row-sha",
            "source_ledger_sha256": "source-sha",
            "blind_web_search_call_count": 2,
            "blind_price_repository_access_count": 1,
            "blind_current_price_access_count": 0,
            "blind_context_mode": "exhaustive",
            "blind_seal_receipt_artifact": receipt.relative_to(tmp_path).as_posix(),
            "blind_seal_receipt_sha256": "bad",
            "phase_state_artifact": phase_state.relative_to(tmp_path).as_posix(),
            "phase_state_sha256": "bad",
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-seal.json: blind_seal_receipt_sha256 mismatch" in findings
    assert "RUN-seal.json: phase_state_sha256 mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt schema_version mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt run_id mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt phase must be BLIND_SEALED" in findings
    assert "RUN-seal.json: blind_seal_receipt blind hash mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt prediction path mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt row_disposition hash mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt source_ledger hash mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt no_d_outcome_exposed must be true" in findings
    assert "RUN-seal.json: blind_seal_receipt validation counts mismatch" in findings
    assert "RUN-seal.json: phase_state schema_version mismatch" in findings
    assert "RUN-seal.json: phase_state run_id mismatch" in findings
    assert "RUN-seal.json: phase_state phase must be BLIND_SEALED" in findings
    assert "RUN-seal.json: phase_state completed phase mismatch" in findings
    assert "RUN-seal.json: phase_state receipt sha mismatch" in findings
    assert "RUN-seal.json: phase_state trade_date mismatch" in findings
    assert "RUN-seal.json: phase_state cutoff_at mismatch" in findings


def test_lookahead_audit_checks_session_pack_context_files(tmp_path: Path) -> None:
    (tmp_path / "session_packs" / "2030-01-10").mkdir(parents=True)
    (tmp_path / "research" / "accepted").mkdir(parents=True)
    (tmp_path / "brain" / "current").mkdir(parents=True)
    write_json(
        tmp_path / "research" / "accepted" / "EP-after-cutoff.json",
        {
            "episode_id": "EP-after-cutoff",
            "available_from": "2030-01-10T09:30:00+09:00",
        },
    )
    (tmp_path / "brain" / "current" / "00_world_model.md").write_text(
        "session pack leak EP-after-cutoff",
        encoding="utf-8",
    )
    write_json(
        tmp_path / "session_packs" / "2030-01-10" / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
            "brain_files": ["brain/current/00_world_model.md"],
            "shard_brain_files": [],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: context file contains future episode "
        "EP-after-cutoff: brain/current/00_world_model.md"
    ) in result["findings"]


def test_lookahead_audit_checks_session_pack_markdown_files(tmp_path: Path) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    (tmp_path / "research" / "accepted").mkdir(parents=True)
    write_json(
        tmp_path / "research" / "accepted" / "EP-after-cutoff.json",
        {
            "episode_id": "EP-after-cutoff",
            "available_from": "2030-01-10T09:30:00+09:00",
        },
    )
    (pack_dir / "memory_cases.md").write_text(
        "unsafe copied memory case EP-after-cutoff",
        encoding="utf-8",
    )
    write_json(
        pack_dir / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: session pack file contains future "
        "episode EP-after-cutoff: memory_cases.md"
    ) in result["findings"]


def test_lookahead_audit_checks_session_pack_temporal_memory_refs(tmp_path: Path) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    company_dir = tmp_path / "memory" / "company_memory"
    company_dir.mkdir(parents=True)
    market_dir = tmp_path / "memory" / "market_memory"
    market_dir.mkdir(parents=True)
    write_json(
        company_dir / "CM-future.json",
        {
            "ticker": "100001",
            "company_name": "FutureMemoryCo",
            "known_at": "2030-01-10T09:30:00+09:00",
        },
    )
    (market_dir / "claims.jsonl").write_text(
        '{"claim_id":"M-future","available_from":"2030-01-10T09:30:00+09:00",'
        '"statement":"future market context"}\n',
        encoding="utf-8",
    )
    write_json(
        pack_dir / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
            "included_company_memory_files": ["memory/company_memory/CM-future.json"],
            "included_market_context_files": ["memory/market_memory/claims.jsonl#L1"],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: included future company memory: "
        "memory/company_memory/CM-future.json"
    ) in result["findings"]
    assert (
        "session_packs/2030-01-10/manifest.json: included future market_context memory: "
        "memory/market_memory/claims.jsonl#L1"
    ) in result["findings"]


def test_lookahead_audit_checks_session_pack_episode_scope(tmp_path: Path) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    pack_files = (
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    )
    for file_name in pack_files:
        (pack_dir / file_name).write_text(f"{file_name} content\n", encoding="utf-8")
    (pack_dir / "omission_report.md").write_text("No omissions.\n", encoding="utf-8")
    pack_file_hashes = {file_name: file_sha256(pack_dir / file_name) for file_name in pack_files}
    token_counts = {
        file_name: max(1, len((pack_dir / file_name).read_text(encoding="utf-8")) // 4)
        for file_name in pack_files
    }
    accepted_dir = tmp_path / "research" / "accepted"
    accepted_dir.mkdir(parents=True)
    write_json(
        accepted_dir / "EP-available-a.json",
        {
            "episode_id": "EP-available-a",
            "available_from": "2030-01-10T08:00:00+09:00",
        },
    )
    write_json(
        accepted_dir / "EP-available-b.json",
        {
            "episode_id": "EP-available-b",
            "available_from": "2030-01-10T08:30:00+09:00",
        },
    )
    write_json(
        accepted_dir / "EP-future.json",
        {
            "episode_id": "EP-future",
            "available_from": "2030-01-10T09:30:00+09:00",
        },
    )
    manifest = {
        "schema_version": "nslab.session_pack_manifest.v1",
        "blocked": False,
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "as_of": "2030-01-10T08:59:59+09:00",
        "mode": "brain",
        "accepted_episode_count": 3,
        "available_episode_count": 2,
        "available_episode_ids": ["EP-available-a"],
        "included_episode_count": 1,
        "included_episode_ids": ["EP-available-a"],
        "budget_omitted_episode_count": 0,
        "budget_omitted_episode_ids": [],
        "available_coverage_complete": True,
        "unavailable_episode_count": 1,
        "omitted_episode_ids": ["EP-future"],
        "unavailable_episode_ids": ["EP-future"],
        "pack_files": list(pack_files),
        "pack_file_count": len(pack_files),
        "pack_file_hashes": pack_file_hashes,
        "pack_sha256": sha256_text(
            "\n".join(pack_file_hashes[file_name] for file_name in pack_files)
        ),
        "token_counts": token_counts,
        "token_count_total": sum(token_counts.values()),
    }
    write_json(pack_dir / "manifest.json", manifest)

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: session pack available episode coverage mismatch"
        in result["findings"]
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack available_episode_ids mismatch"
        in result["findings"]
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack budget_omitted_episode_ids mismatch"
        in result["findings"]
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack available_coverage_complete mismatch"
        in result["findings"]
    )

    write_json(
        pack_dir / "manifest.json",
        {
            **manifest,
            "available_episode_ids": ["EP-available-a", "EP-available-b"],
            "included_episode_count": 2,
            "included_episode_ids": ["EP-available-a", "EP-available-b"],
            "budget_omitted_episode_count": 0,
            "budget_omitted_episode_ids": [],
            "available_coverage_complete": True,
        },
    )

    clean = audit_lookahead(tmp_path)

    assert clean["passed"], clean["findings"]


def test_lookahead_audit_verifies_session_pack_file_hashes(tmp_path: Path) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    pack_files = (
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    )
    for file_name in pack_files:
        (pack_dir / file_name).write_text(f"{file_name} content\n", encoding="utf-8")
    (pack_dir / "omission_report.md").write_text("No omissions.\n", encoding="utf-8")
    pack_file_hashes = {file_name: file_sha256(pack_dir / file_name) for file_name in pack_files}
    token_counts = {
        file_name: max(1, len((pack_dir / file_name).read_text(encoding="utf-8")) // 4)
        for file_name in pack_files
    }
    write_json(
        pack_dir / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
            "pack_files": list(pack_files),
            "pack_file_count": len(pack_files),
            "pack_file_hashes": pack_file_hashes,
            "pack_sha256": sha256_text(
                "\n".join(pack_file_hashes[file_name] for file_name in pack_files)
            ),
            "omission_report_file": "omission_report.md",
            "omission_report_sha256": file_sha256(pack_dir / "omission_report.md"),
            "token_counts": token_counts,
            "token_count_total": sum(token_counts.values()),
        },
    )

    clean = audit_lookahead(tmp_path)

    assert clean["passed"], clean["findings"]

    manifest = read_json(pack_dir / "manifest.json")
    write_json(pack_dir / "manifest.json", {**manifest, "token_count_total": 1})
    bad_token_total = audit_lookahead(tmp_path)

    assert not bad_token_total["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: token_count_total mismatch"
        in bad_token_total["findings"]
    )
    write_json(pack_dir / "manifest.json", manifest)

    write_json(pack_dir / "manifest.json", {**manifest, "omission_report_sha256": "bad"})
    bad_omission_report = audit_lookahead(tmp_path)

    assert not bad_omission_report["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: omission_report_sha256 mismatch"
        in bad_omission_report["findings"]
    )
    write_json(pack_dir / "manifest.json", manifest)

    write_json(
        pack_dir / "manifest.json",
        {
            **manifest,
            "blocked": False,
            "accepted_episode_count": 0,
            "available_episode_count": 0,
            "available_episode_ids": [],
            "included_episode_count": 0,
            "included_episode_ids": [],
            "omitted_episode_ids": [],
            "unavailable_episode_ids": [],
            "token_budget": sum(token_counts.values()) - 1,
        },
    )
    unblocked_over_budget = audit_lookahead(tmp_path)

    assert not unblocked_over_budget["passed"]
    assert (
        "session_packs/2030-01-10/manifest.json: session pack token budget exceeded without blocked"
        in unblocked_over_budget["findings"]
    )
    write_json(pack_dir / "manifest.json", manifest)

    (pack_dir / "current_news.md").write_text("tampered current news\n", encoding="utf-8")

    tampered = audit_lookahead(tmp_path)

    assert not tampered["passed"]
    findings = tampered["findings"]
    assert isinstance(findings, list)
    assert (
        "session_packs/2030-01-10/manifest.json: pack_file_hashes mismatch: "
        "current_news.md"
    ) in findings
    assert "session_packs/2030-01-10/manifest.json: pack_sha256 mismatch" in findings


def test_lookahead_audit_requires_session_pack_reproducibility_hashes(
    tmp_path: Path,
) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    for file_name in (
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    ):
        (pack_dir / file_name).write_text(f"{file_name} content\n", encoding="utf-8")
    write_json(
        pack_dir / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "blocked": False,
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "session_packs/2030-01-10/manifest.json: pack_files missing" in findings
    assert "session_packs/2030-01-10/manifest.json: pack_file_count missing" in findings
    assert (
        "session_packs/2030-01-10/manifest.json: pack_file_hashes is invalid"
        in findings
    )
    assert "session_packs/2030-01-10/manifest.json: missing pack_sha256" in findings


def test_lookahead_audit_checks_session_pack_news_window(tmp_path: Path) -> None:
    pack_dir = tmp_path / "session_packs" / "2030-01-10"
    pack_dir.mkdir(parents=True)
    news_csv = tmp_path / "news.csv"
    news_csv.write_text(
        "page,row,date,time,title,body\n"
        '1,1,"2030-01-09","15:30:00","Inside window","Safe news."\n'
        '1,2,"2030-01-10","09:00:00","After cutoff","Unsafe news."\n',
        encoding="utf-8",
    )
    batch = load_news_csv(news_csv, trade_date=date(2030, 1, 10))
    current_news = (
        f"## {batch.items[0].event_id}\n{batch.items[0].title}\n\n{batch.items[0].body}"
        f"\n\n## {batch.items[1].event_id}\n{batch.items[1].title}\n\n{batch.items[1].body}"
    )
    (pack_dir / "current_news.md").write_text(current_news, encoding="utf-8")
    for file_name in (
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "company_memory.md",
        "market_context.md",
    ):
        (pack_dir / file_name).write_text(f"{file_name} content\n", encoding="utf-8")
    pack_files = (
        "system_instructions.md",
        "research_brain.md",
        "memory_cases.md",
        "current_news.md",
        "company_memory.md",
        "market_context.md",
    )
    pack_file_hashes = {
        file_name: file_sha256(pack_dir / file_name) for file_name in pack_files
    }
    token_counts = {
        file_name: max(1, len((pack_dir / file_name).read_text(encoding="utf-8")) // 4)
        for file_name in pack_files
    }
    write_json(
        pack_dir / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
            "news_file": "news.csv",
            "news_sha256": file_sha256(news_csv),
            "news_window_start_at": "2030-01-09T15:30:00+09:00",
            "news_window_end_at": "2030-01-10T08:59:59+09:00",
            "news_row_count": 2,
            "included_news_row_count": 2,
            "excluded_news_row_count": 0,
            "current_news_event_ids": [item.event_id for item in batch.items],
            "excluded_news_event_ids": [],
            "pack_files": list(pack_files),
            "pack_file_count": len(pack_files),
            "pack_file_hashes": pack_file_hashes,
            "pack_sha256": sha256_text(
                "\n".join(pack_file_hashes[file_name] for file_name in pack_files)
            ),
            "token_counts": token_counts,
            "token_count_total": sum(token_counts.values()),
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert (
        "session_packs/2030-01-10/manifest.json: session pack included_news_row_count mismatch"
        in findings
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack excluded_news_row_count mismatch"
        in findings
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack current_news_event_ids mismatch"
        in findings
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack excluded_news_event_ids mismatch"
        in findings
    )
    assert (
        "session_packs/2030-01-10/manifest.json: session pack current_news.md content mismatch"
        in findings
    )


def test_lookahead_audit_checks_daily_manifest_company_memory_refs(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    company_dir = tmp_path / "memory" / "company_memory"
    company_dir.mkdir(parents=True)
    write_json(
        company_dir / "CM-future.json",
        {
            "ticker": "100001",
            "company_name": "FutureMemoryCo",
            "known_at": "2030-01-10T09:30:00+09:00",
        },
    )
    write_json(
        manifest_dir / "RUN-company-memory.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-company-memory",
            "mode": "brain",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "included_company_memory_files": ["memory/company_memory/CM-future.json"],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "RUN-company-memory.json: included future company memory: "
        "memory/company_memory/CM-future.json"
    ) in result["findings"]


def test_lookahead_audit_checks_daily_manifest_market_memory_refs(tmp_path: Path) -> None:
    manifest_dir = tmp_path / "runs" / "manifests"
    manifest_dir.mkdir(parents=True)
    market_dir = tmp_path / "memory" / "market_memory"
    market_dir.mkdir(parents=True)
    (market_dir / "claims.jsonl").write_text(
        '{"claim_id":"M-future","available_from":"2030-01-10T09:30:00+09:00",'
        '"statement":"future market context"}\n',
        encoding="utf-8",
    )
    write_json(
        manifest_dir / "RUN-market-memory.json",
        {
            "schema_version": "nslab.context_manifest.v1",
            "run_id": "RUN-market-memory",
            "mode": "brain",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "included_market_context_files": ["memory/market_memory/claims.jsonl#L1"],
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    assert (
        "RUN-market-memory.json: included future market_context memory: "
        "memory/market_memory/claims.jsonl#L1"
    ) in result["findings"]
