from __future__ import annotations

import asyncio
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import ContextManifest, PriceSnapshot
from news_scalping_lab.contracts.shadow_evaluation import ShadowExecutionIdentity
from news_scalping_lab.diagnostics import build_doctor_report, production_readiness_report
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.policies import EvidencePolicy, web_required_for_policy
from news_scalping_lab.utils import KST, write_json
from news_scalping_lab.web.postclose import run_postclose_web_audit
from news_scalping_lab.web.provider import (
    DisabledWebResearchProvider,
    MockWebResearchProvider,
    UnexpectedWebAccessError,
)


class _WebSpy(DisabledWebResearchProvider):
    def __init__(self) -> None:
        self.calls = 0

    async def search(self, query: str, *, cutoff_at: datetime):  # type: ignore[no-untyped-def]
        self.calls += 1
        return await super().search(query, cutoff_at=cutoff_at)


def _manifest(**updates: object) -> ContextManifest:
    cutoff = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    payload = {
        "run_id": "RUN-policy",
        "mode": "exhaustive",
        "trade_date": date(2030, 1, 10),
        "cutoff_at": cutoff,
        "as_of": cutoff,
        "accepted_episode_count": 0,
        "swept_episode_count": 0,
        "price_snapshot": PriceSnapshot(
            source_name="stock-web",
            allowed_through=date(2030, 1, 9),
        ),
        **updates,
    }
    return ContextManifest.model_validate(payload)


def test_csv_memory_only_blind_never_calls_web(tmp_path: Path) -> None:
    spy = _WebSpy()
    settings = Settings(
        project_root=tmp_path,
        evidence_policy=EvidencePolicy.CSV_MEMORY_ONLY_STRICT,
        web_provider="disabled",
    )
    analyzer = DailyAnalyzer(settings, web_provider=spy)
    with pytest.raises(UnexpectedWebAccessError, match="BLIND analysis"):
        asyncio.run(
            analyzer.analyze(
                news_csv=tmp_path / "missing.csv",
                trade_date=date(2030, 1, 10),
                cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
                web_search=True,
            )
        )
    assert spy.calls == 0


def test_csv_memory_only_final_synthesis_contains_no_web_evidence() -> None:
    manifest = _manifest()
    assert manifest.web_sources == []
    assert manifest.candidate_web_source_ids == []
    assert manifest.external_web_evidence_count == 0


def test_csv_memory_only_does_not_require_brave_key(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        evidence_policy=EvidencePolicy.CSV_MEMORY_ONLY_STRICT,
        web_provider="disabled",
    )
    report = build_doctor_report(settings, production=True)
    assert report["api_connections"]["brave_search"]["required"] is False
    assert report["api_connections"]["brave_search"]["status"] == "not_required"


def test_doctor_accepts_disabled_web_under_csv_memory_only(tmp_path: Path) -> None:
    settings = Settings(
        project_root=tmp_path,
        evidence_policy=EvidencePolicy.CSV_MEMORY_ONLY_STRICT,
        web_provider="disabled",
    )
    report = production_readiness_report(
        build_doctor_report(settings, production=True),
        settings,
    )
    assert not any("Brave" in finding for finding in report["findings"])
    assert not any("unsupported production provider disabled" in finding for finding in report["findings"])
    assert report["web_required"] is False


def test_doctor_rejects_web_call_under_csv_memory_only() -> None:
    with pytest.raises(ValidationError, match="forbids BLIND web calls"):
        _manifest(blind_web_search_call_count=1)


def test_all_shadow_arms_share_same_evidence_policy() -> None:
    identity = ShadowExecutionIdentity(
        execution_mode="SYNTHETIC_CONTRACT",
        runner_protocol_version="runner.v1",
        llm_provider="mock",
        llm_model="mock",
        prompt_version="prompt.v1",
        inference_config_sha256="a" * 64,
        started_at="2030-01-10T08:00:00+09:00",
        completed_at="2030-01-10T08:01:00+09:00",
        production_provider_attested=False,
    )
    assert identity.evidence_policy == "csv-memory-only-strict"
    assert identity.web_provider == "disabled"
    assert identity.web_required is False


def test_postclose_optional_web_cannot_mutate_blind_prediction(
    tmp_path: Path,
) -> None:
    prediction = tmp_path / "predictions" / "2030-01-10.json"
    write_json(prediction, {"prediction_id": "PRED-sealed"})
    before = prediction.read_bytes()
    artifact, audit_path = asyncio.run(
        run_postclose_web_audit(
            tmp_path,
            trade_date=date(2030, 1, 10),
            queries=["post-close verification"],
            provider=MockWebResearchProvider(),
            available_from=datetime(2030, 1, 10, 16, 0, tzinfo=KST),
        )
    )
    assert prediction.read_bytes() == before
    assert audit_path.is_file()
    assert artifact["blind_prediction_mutated"] is False
    assert artifact["training_record_created"] is False


def test_postclose_optional_policy_does_not_require_blind_web() -> None:
    assert (
        web_required_for_policy(EvidencePolicy.POSTCLOSE_WEB_AUDIT_OPTIONAL)
        is False
    )
