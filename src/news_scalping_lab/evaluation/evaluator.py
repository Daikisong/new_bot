"""Post-close evaluation."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from news_scalping_lab.config import Settings, load_settings
from news_scalping_lab.contracts.models import FailureCode, Postmortem
from news_scalping_lab.prices.base import PriceSource
from news_scalping_lab.prices.factory import create_price_source
from news_scalping_lab.utils import now_kst, read_json, write_json
from news_scalping_lab.warehouse import WarehouseStore


class Evaluator:
    def __init__(self, root: Path, price_source: PriceSource | None = None) -> None:
        self.root = root
        settings = load_settings(root)
        self.price_source = price_source or create_price_source(
            settings if settings.project_root == root.resolve() else Settings(project_root=root)
        )

    def evaluate(self, *, trade_date: date) -> Path:
        prediction_path = self.root / "predictions" / f"{trade_date.isoformat()}.json"
        if not prediction_path.exists():
            raise FileNotFoundError(f"blind prediction not found: {prediction_path}")
        prediction = read_json(prediction_path)
        outcomes: dict[str, object] = {}
        hits: list[str] = []
        false_positives: list[str] = []
        for candidate in prediction.get("candidates", []):
            ticker = str(candidate.get("ticker", "UNKNOWN"))
            company = str(candidate.get("company_name", "UNKNOWN"))
            outcome = self.price_source.get_outcome(ticker, trade_date=trade_date)
            outcomes[company] = outcome.model_dump(mode="json")
            if outcome.upper_limit_touched:
                hits.append(company)
            else:
                false_positives.append(company)
        postmortem = Postmortem(
            summary="Mock postmortem generated from sealed blind prediction and evaluation-only outcomes.",
            hits=hits,
            misses=[],
            false_positives=false_positives,
            failure_codes=[FailureCode.UNKNOWN] if false_positives else [],
            lessons=[
                "Use postmortem lessons only from the next trading day forward.",
                "Do not rewrite sealed blind reasoning after outcomes are known.",
            ],
        )
        output = {
            "schema_version": "nslab.evaluation.v1",
            "trade_date": trade_date.isoformat(),
            "created_at": now_kst().isoformat(),
            "blind_prediction_id": prediction.get("prediction_id"),
            "outcomes": outcomes,
            "postmortem": postmortem.model_dump(mode="json"),
        }
        target = self.root / "reports" / f"{trade_date.isoformat()}_postmortem.json"
        write_json(target, output)
        WarehouseStore(self.root).write_daily_outcomes_from_files()
        return target
