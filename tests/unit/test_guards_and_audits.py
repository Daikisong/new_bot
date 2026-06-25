from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import NoReturn

import pytest

from news_scalping_lab.audits.hardcoding import audit_hardcoding
from news_scalping_lab.audits.lookahead import audit_lookahead
from news_scalping_lab.audits.provenance import audit_provenance
from news_scalping_lab.prices.base import (
    BlindPriceAccessError,
    BlindPriceGuard,
    PriceRecord,
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


def _trace_payload(*, prompt_sha256: str = "blind-hash") -> dict[str, object]:
    trace_input = {
        "prompt_sha256": prompt_sha256,
        "prompt_chars": 100,
        "response_model": "BlindPrediction",
    }
    output = {"prediction_id": "PRED-linked"}
    return {
        "trace_id": "TRACE-linked",
        "operation": "generate_structured",
        "purpose": "daily_blind_analysis",
        "status": "ok",
        "provider": "DeterministicMockLLMProvider",
        "model_config": {"provider": "mock"},
        "input": trace_input,
        "input_sha256": sha256_text(canonical_json(trace_input)),
        "output": output,
        "output_sha256": sha256_text(canonical_json(output)),
        "checkpoint_id": "LLMCKPT-linked",
        "tool_calls": [],
        "retries": 0,
        "token_usage": {"prompt_tokens_estimate": 25, "completion_tokens_estimate": 10},
        "started_at": "2030-01-10T08:59:00+09:00",
        "finished_at": "2030-01-10T08:59:01+09:00",
        "prompt_version": "daily_blind_analysis.v1",
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


def test_hardcoding_audit_passes_current_source() -> None:
    root = Path(__file__).resolve().parents[2]
    result = audit_hardcoding(root)
    assert result["passed"], result["findings"]


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


def test_hardcoding_audit_flags_hangul_candidate_collections(tmp_path: Path) -> None:
    source_dir = tmp_path / "src" / "news_scalping_lab"
    source_dir.mkdir(parents=True)
    (source_dir / "candidate_lists.py").write_text(
        """
CANDIDATES = ["가상전자", "샘플모빌리티"]
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
        "Run ID: `RUN-missing`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert not result["passed"]
    assert "2030-01-10.json: missing context_manifest_id" in result["findings"]


def test_provenance_audit_accepts_manifest_and_report_links(tmp_path: Path) -> None:
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
    write_json(
        tmp_path / "predictions" / "2030-01-10.json",
        prediction,
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    write_json(run_prediction_path, prediction)
    run_report_path.write_text("Run ID: `RUN-linked`", encoding="utf-8")
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
    (tmp_path / "reports" / "2030-01-10_preopen.md").write_text(
        "Run ID: `RUN-linked`", encoding="utf-8"
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]


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
        "Run ID: `RUN-linked`", encoding="utf-8"
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
        "Run ID: `RUN-linked`", encoding="utf-8"
    )
    run_output_dir = tmp_path / "runs" / "checkpoints" / "output_artifacts" / "RUN-linked"
    run_prediction_path = run_output_dir / "blind_prediction.json"
    run_report_path = run_output_dir / "preopen_report.md"
    bad_run_prediction = {**prediction, "context_manifest_id": "RUN-other"}
    write_json(run_prediction_path, bad_run_prediction)
    run_report_path.write_text("Run ID: `RUN-other`", encoding="utf-8")
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
    sweep_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "memory_sweep"
        / "RUN-linked"
        / "shard_0001.json"
    )
    sweep_ref = sweep_path.relative_to(tmp_path).as_posix()
    sweep_payload = {
        "schema_version": "nslab.memory_sweep_contribution.v1",
        "cache_key": "SWEEP-linked",
        "mode": "exhaustive",
        "trade_date": "2030-01-10",
        "cutoff_at": "2030-01-10T08:59:59+09:00",
        "brain_version": "brain-linked",
        "episode_count": 2,
        "episode_ids": ["EP-sweep-1", "EP-sweep-2"],
        "from_cache": False,
    }
    write_json(sweep_path, sweep_payload)
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
        "2030-01-10.json: context manifest memory_sweep swept episode ids mismatch"
    ) in findings


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
    episode_path = tmp_path / "research" / "accepted" / "EP-semantic.json"
    write_json(
        episode_path,
        {
            "episode_id": "EP-semantic",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "created_at": "2030-01-10T09:00:00+09:00",
            "research_version": "semantic-test",
            "input_news_files": [],
            "input_news_hashes": [],
            "input_audit": {
                "semantic_import": {
                    "prompt_version": "semantic_import.v1",
                    "source_path": raw_path.as_posix(),
                    "source_sha256": source_hash,
                    "source_text_sha256": sha256_text(raw_text),
                    "source_segment_count": len(source_segments),
                    "source_segments_sha256": sha256_text(canonical_json(source_segments)),
                    "source_segments": source_segments,
                    "output_field_source_ids": {
                        "blind_analysis.summary": [source_id],
                    },
                }
            },
            "price_source_snapshot": {"source": "test"},
            "blind_analysis": {
                "summary": "Imported from source.",
                "open_world_mechanisms": [],
                "initial_uncertainties": [],
                "provenance": [provenance],
            },
            "blind_predictions": [],
            "observed_events": [],
            "event_ticker_edges": [],
            "lessons": [],
            "counterexamples": [],
            "misses": [],
            "provenance": [provenance],
            "available_from": "2030-01-11T00:00:00+09:00",
        },
    )

    result = audit_provenance(tmp_path)

    assert result["passed"], result["findings"]
    assert result["checked_research_episode_files"] == 1

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
        },
    )
    write_json(
        tmp_path / "runs" / "checkpoints" / "red_team" / "RUN-linked.json",
        {
            "schema_version": "nslab.red_team_artifact.v1",
            "run_id": "RUN-linked",
            "source_prediction_id": "PRED-linked",
            "prompt_version": "red_team.candidate_attack.v1",
            "prompt_sha256": "red-team-hash",
            "created_at": "2030-01-10T08:59:59+09:00",
            "candidate_count": 1,
            "candidate_findings": [{"candidate_rank": 1, "passed_to_synthesis": True}],
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
            "prompt_version": "red_team.candidate_attack.v1",
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
                "verification_focus": ["listed_security_and_exact_ticker"],
                "source_id": "WEB-CANDIDATE-FUTURE",
                "query": "candidate verification",
                "title": "future source",
                "url": "https://example.test/future",
                "source_url": "https://example.test/future",
                "snippet": "future",
                "published_at": "2030-01-10T09:30:00+09:00",
                "retrieved_at": "2030-01-10T09:31:00+09:00",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "time_verified": False,
                "available_before_cutoff": False,
                "content_sha256": "abc",
                "opened_text": "raw opened text must not be copied",
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
                "source_id": "WEB-CANDIDATE-EXCLUDED",
                "query": "candidate verification",
                "title": "excluded source",
                "url": "https://example.test/excluded",
                "source_url": "https://example.test/excluded",
                "snippet": "excluded",
                "published_at": "2030-01-10T08:30:00+09:00",
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
            "excluded_candidate_web_source_ids": ["WEB-CANDIDATE-OTHER-EXCLUDED"],
            "excluded_candidate_web_check_artifact": excluded_artifact.relative_to(
                tmp_path
            ).as_posix(),
            "excluded_candidate_web_check_sha256": "bad",
            "excluded_candidate_web_check_count": 2,
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert "RUN-candidate.json: candidate_web_check:1 is not cutoff verified" in findings
    assert "RUN-candidate.json: candidate_web_check:1 after cutoff" in findings
    assert (
        "RUN-candidate.json: candidate_web_check:1 must not duplicate opened_text"
        in findings
    )
    assert "RUN-candidate.json: candidate_web_source_ids do not match artifact" in findings
    assert "RUN-candidate.json: candidate_web_check_count mismatch" in findings
    assert "RUN-candidate.json: excluded_candidate_web_check_sha256 mismatch" in findings
    assert (
        "RUN-candidate.json: excluded_candidate_web_check:1 is cutoff verified"
        in findings
    )
    assert (
        "RUN-candidate.json: excluded_candidate_web_source_ids do not match artifact"
        in findings
    )
    assert "RUN-candidate.json: excluded_candidate_web_check_count mismatch" in findings


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
        },
    )

    result = audit_lookahead(tmp_path)

    assert not result["passed"]
    findings = result["findings"]
    assert isinstance(findings, list)
    assert "RUN-ledger.json: source_ledger_sha256 mismatch" in findings
    assert "RUN-ledger.json: source_ledger:1 must not duplicate body/content" in findings
    assert "RUN-ledger.json: source_ledger:1 source_url mismatch" in findings
    assert "RUN-ledger.json: source_ledger:1 BLIND source after cutoff" in findings
    assert any(
        finding.startswith("RUN-ledger.json: source_ledger:2 missing fields:")
        for finding in findings
    )
    assert "RUN-ledger.json: source_ledger:2 invalid usage_phase" in findings
    assert "RUN-ledger.json: source_ledger duplicate source_id" in findings
    assert "RUN-ledger.json: source_ledger entry_count mismatch" in findings


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
            "schema_version": "nslab.blind_seal_receipt.v1",
            "run_id": "RUN-seal",
            "phase": "OPEN",
            "blind_artifact_sha256": "other",
            "no_d_outcome_exposed": False,
        },
    )
    write_json(
        phase_state,
        {
            "schema_version": "nslab.phase_state.v1",
            "run_id": "RUN-seal",
            "phase": "OPEN",
            "blind_seal_receipt_sha256": "wrong",
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
    assert "RUN-seal.json: blind_seal_receipt phase must be BLIND_SEALED" in findings
    assert "RUN-seal.json: blind_seal_receipt blind hash mismatch" in findings
    assert "RUN-seal.json: blind_seal_receipt no_d_outcome_exposed must be true" in findings
    assert "RUN-seal.json: phase_state phase must be BLIND_SEALED" in findings
    assert "RUN-seal.json: phase_state receipt sha mismatch" in findings


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
    pack_file_hashes = {file_name: file_sha256(pack_dir / file_name) for file_name in pack_files}
    write_json(
        pack_dir / "manifest.json",
        {
            "schema_version": "nslab.session_pack_manifest.v1",
            "trade_date": "2030-01-10",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "as_of": "2030-01-10T08:59:59+09:00",
            "mode": "brain",
            "pack_file_hashes": pack_file_hashes,
            "pack_sha256": sha256_text(
                "\n".join(pack_file_hashes[file_name] for file_name in pack_files)
            ),
        },
    )

    clean = audit_lookahead(tmp_path)

    assert clean["passed"], clean["findings"]

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
