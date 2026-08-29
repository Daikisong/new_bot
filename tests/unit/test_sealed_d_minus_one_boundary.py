from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

import news_scalping_lab.inference.analyzer as analyzer_module
from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import Candidate, PathType
from news_scalping_lab.contracts.quality_evaluation import (
    DMinusOnePromptProjection,
    QualityArtifactReference,
    SharedDMinusOneContext,
    SharedDMinusOneSnapshot,
)
from news_scalping_lab.evaluation.quality_runtime import (
    BLIND_INPUT_ROOT,
    _build_privileged_d_minus_one_context,
)
from news_scalping_lab.inference.analyzer import DailyAnalyzer
from news_scalping_lab.prices.base import PriceRecord
from news_scalping_lab.prices.stock_web import StockWebPriceSource
from news_scalping_lab.utils import KST, canonical_json, file_sha256, sha256_text


class _SnapshotSource:
    source_name = "sealed-boundary-test"

    def __init__(
        self,
        records: list[PriceRecord],
        *,
        revision: str = "a" * 64,
    ) -> None:
        self.records = records
        self.source_revision_sha256 = revision
        self.calls: list[date] = []

    def get_blind_snapshot_universe(self, *, through: date) -> list[PriceRecord]:
        self.calls.append(through)
        return list(self.records)


def _context(*, close: float = 100.0) -> SharedDMinusOneContext:
    return _build_privileged_d_minus_one_context(
        _SnapshotSource(
            [
                PriceRecord(
                    ticker="000001",
                    trade_date=date(2030, 1, 9),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=1.0,
                    amount=close,
                    market_cap=close * 10,
                    listed_shares=10.0,
                )
            ]
        ),
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_sealed_snapshot_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(ValidationError, match="finite_number"):
        SharedDMinusOneSnapshot(
            ticker="000001",
            trade_date=date(2030, 1, 9),
            close=value,
        )


@pytest.mark.parametrize(
    "extra_payload",
    [
        {"D_day_outcome": {"return_pct": 99.0}},
        {"metadata": {"next_day_return_pct": 99.0}},
        {"winner": True},
        {"postmortem_label": "hit"},
    ],
)
def test_sealed_snapshot_rejects_top_or_nested_result_aliases(
    extra_payload: dict[str, object],
) -> None:
    payload = {
        "ticker": "000001",
        "trade_date": "2030-01-09",
        "open": 100.0,
        "high": 100.0,
        "low": 100.0,
        "close": 100.0,
        "volume": 1.0,
        "amount": 100.0,
        "market_cap": 1000.0,
        "listed_shares": 10.0,
        **extra_payload,
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SharedDMinusOneSnapshot.model_validate(payload)


def test_privileged_builder_seals_only_latest_market_session() -> None:
    source = _SnapshotSource(
        [
            PriceRecord("000001", date(2030, 1, 9), close=100.0),
            PriceRecord("STALE-TICKER", date(2020, 1, 9), close=10.0),
        ]
    )
    context = _build_privileged_d_minus_one_context(
        source,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    encoded = canonical_json(context.model_dump(mode="json"))
    assert source.calls == [date(2030, 1, 9)]
    assert context.snapshot_session_date == date(2030, 1, 9)
    assert context.candidate_universe == ["000001"]
    assert context.sealed_snapshot_count == 1
    assert context.privileged_source_snapshot_count == 2
    assert context.price_repository_access_count == 0
    assert context.skipped_tickers == []
    assert "STALE-TICKER" not in encoded


def test_privileged_builder_rejects_d_day_or_future_rows() -> None:
    source = _SnapshotSource(
        [PriceRecord("000001", date(2030, 1, 10), close=100.0)]
    )
    with pytest.raises(ValueError, match="D-day or future snapshot"):
        _build_privileged_d_minus_one_context(
            source,
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )


def test_prediction_analyzer_loads_sealed_d1_with_zero_stock_web_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("prediction touched the StockWeb repository")

    monkeypatch.setattr(analyzer_module, "create_price_source", forbidden)
    monkeypatch.setattr(StockWebPriceSource, "__init__", forbidden)
    monkeypatch.setattr(
        StockWebPriceSource,
        "get_blind_snapshot_universe",
        forbidden,
    )
    monkeypatch.setattr(StockWebPriceSource, "_known_tickers", forbidden)
    monkeypatch.setattr(StockWebPriceSource, "_iter_records", forbidden)

    context = _context()
    payload = canonical_json(context.model_dump(mode="json")) + "\n"
    input_id = "QINPUT-" + sha256_text(payload)[:20]
    path = tmp_path / BLIND_INPUT_ROOT / input_id / "d_minus_one_safe_context.json"
    path.parent.mkdir(parents=True)
    path.write_text(payload, encoding="utf-8")
    reference = QualityArtifactReference(
        artifact_path=path.relative_to(tmp_path).as_posix(),
        sha256=file_sha256(path),
    )

    analyzer = DailyAnalyzer(
        Settings(project_root=tmp_path, price_provider="stock-web"),
        configure_price_source=False,
    )
    loaded, loaded_sha256, loaded_path = analyzer._load_shared_d_minus_one_context(
        path=path,
        expected_artifact_path=reference.artifact_path,
        expected_sha256=reference.sha256,
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )

    assert analyzer.price_source is None
    assert loaded == context
    assert loaded_sha256 == reference.sha256
    assert loaded_path == path.resolve()


def test_daily_analyzer_production_default_still_configures_price_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = _SnapshotSource([])
    calls: list[Settings] = []

    def configured(settings: Settings) -> object:
        calls.append(settings)
        return sentinel

    monkeypatch.setattr(analyzer_module, "create_price_source", configured)
    analyzer = DailyAnalyzer(
        Settings(project_root=tmp_path, price_provider="stock-web")
    )

    assert calls == [analyzer.settings]
    assert analyzer.price_source is sentinel


@pytest.mark.asyncio
async def test_sealed_d1_only_analyzer_fails_closed_without_package(
    tmp_path: Path,
) -> None:
    analyzer = DailyAnalyzer(
        Settings(project_root=tmp_path),
        configure_price_source=False,
    )

    with pytest.raises(ValueError, match="complete immutable QUALITY_FULL input package"):
        await analyzer.analyze(
            news_csv=tmp_path / "missing.csv",
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )


def test_sealed_d1_loader_rejects_recursive_result_key_before_model_coercion(
    tmp_path: Path,
) -> None:
    payload = _context().model_dump(mode="json")
    snapshot = payload["snapshots"][0]
    assert isinstance(snapshot, dict)
    snapshot["metadata"] = {"D_day_outcome": {"return_pct": 99.0}}
    encoded = json.dumps(payload, sort_keys=True) + "\n"
    path = (
        tmp_path
        / BLIND_INPUT_ROOT
        / ("QINPUT-" + "f" * 20)
        / "d_minus_one_safe_context.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(encoded, encoding="utf-8")

    analyzer = DailyAnalyzer(Settings(project_root=tmp_path))
    with pytest.raises(ValueError, match="forbidden outcome fields"):
        analyzer._load_shared_d_minus_one_context(
            path=path,
            expected_artifact_path=path.relative_to(tmp_path).as_posix(),
            expected_sha256=file_sha256(path),
            trade_date=date(2030, 1, 10),
            cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
        )


def test_d1_prompt_projection_is_exact_candidate_subset_and_bounded() -> None:
    records = [
        PriceRecord(
            ticker=f"{index:06d}",
            trade_date=date(2030, 1, 9),
            close=float(index + 1),
        )
        for index in range(2_800)
    ]
    full_context = _build_privileged_d_minus_one_context(
        _SnapshotSource(records),
        trade_date=date(2030, 1, 10),
        cutoff_at=datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST),
    )
    reference = QualityArtifactReference(
        artifact_path=(
            "runs/semantic_brain_upgrade/quality_full/blind_inputs/"
            "QINPUT-test/d_minus_one_safe_context.json"
        ),
        sha256="b" * 64,
    )
    candidates = [
        Candidate(
            rank=1,
            ticker="000001",
            company_name="present",
            path_type=PathType.SINGLE_EVENT,
            event_ids=["EVENT-1"],
            thesis="present thesis",
            why_now="present now",
        ),
        Candidate(
            rank=2,
            ticker="999999",
            company_name="missing",
            path_type=PathType.THEME_BENEFICIARY,
            event_ids=["EVENT-2"],
            thesis="missing thesis",
            why_now="missing now",
        ),
    ]

    projection = DailyAnalyzer._build_d_minus_one_prompt_projection(
        context=full_context,
        context_reference=reference,
        candidates=candidates,
    )
    projection_payload = projection.model_dump(mode="json")
    projection_text = canonical_json(projection_payload)
    full_text = canonical_json(full_context.model_dump(mode="json"))

    assert projection.requested_tickers == ["000001", "999999"]
    assert [row.ticker for row in projection.snapshots] == ["000001"]
    assert projection.missing_tickers == ["999999"]
    assert projection.full_snapshot_count == 2_800
    assert "002799" not in projection_text
    assert len(projection_text.encode("utf-8")) < 80_000
    assert len(full_text.encode("utf-8")) > len(projection_text.encode("utf-8")) * 50

    tampered = dict(projection_payload)
    tampered["missing_tickers"] = []
    with pytest.raises(ValidationError, match="dispose every requested ticker"):
        DMinusOnePromptProjection.model_validate(tampered)
