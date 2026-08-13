"""Deterministic outcome projections and population statistics."""

from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from news_scalping_lab.records.models import BrainRecordEnvelope

POPULATION_STATISTICS_VERSION = "population_statistics.v1"
BLOCK_BOOTSTRAP_VERSION = "trade_date_block_bootstrap.v1"
DEFAULT_BOOTSTRAP_ITERATIONS = 1_000

_OUTCOME_CONTAINERS = (
    "payload",
    "D_outcome",
    "D_response",
    "outcome",
    "issuer_day_outcome",
    "fields",
)
_HIGH_RETURN_FIELDS = (
    "outcome_high_return_pct",
    "high_return_pct",
    "intraday_high_return_pct",
    "D_high_return_pct",
)
_CLOSE_RETURN_FIELDS = (
    "outcome_close_return_pct",
    "close_return_pct",
    "D_close_return_pct",
)
_UPPER_LIMIT_FIELDS = (
    "upper_limit_touched",
    "is_upper_limit",
    "upper_limit",
)
_PATH_FIELDS = ("path_type", "relation_path_type", "candidate_path_type")
_REGIME_FIELDS = (
    "regime_cluster",
    "market_regime_cluster",
    "regime_label",
    "market_regime",
)
_WEIGHT_FIELDS = ("sample_weight", "training_weight", "unit_weight")
_OUTCOME_OBSERVED_FIELDS = (
    "outcome_observed",
    "has_outcome",
    "outcome_available",
)


@dataclass(frozen=True)
class PopulationRecordProjection:
    path_type: str
    regime_cluster: str
    high_return_pct: float | None
    close_return_pct: float | None
    upper_limit_touched: bool | None
    outcome_observed: bool
    sample_weight: float
    high_return_status: str
    close_return_status: str
    upper_limit_status: str
    sample_weight_status: str


@dataclass(frozen=True)
class UnitObservation:
    independent_unit_id: str
    trade_date: date
    record_ids: tuple[str, ...]
    cell_ids: tuple[str, ...]
    memory_lanes: tuple[str, ...]
    record_types: tuple[str, ...]
    path_types: tuple[str, ...]
    regime_clusters: tuple[str, ...]
    polarity: str
    eligibility: str
    label_quality: str
    sample_weight: float
    high_return_pct: float | None
    close_return_pct: float | None
    upper_limit_touched: bool | None
    high_return_status: str
    close_return_status: str
    upper_limit_status: str
    sample_weight_status: str

    def __post_init__(self) -> None:
        for value, status, name in (
            (self.high_return_pct, self.high_return_status, "high_return"),
            (self.close_return_pct, self.close_return_status, "close_return"),
            (self.upper_limit_touched, self.upper_limit_status, "upper_limit"),
        ):
            if (value is not None) is not (status == "VALID"):
                raise ValueError(f"unit {name} value conflicts with status")
        if (self.sample_weight > 0.0) is not (
            self.sample_weight_status in {"VALID", "DEFAULT"}
        ):
            raise ValueError("unit sample weight conflicts with status")

    @property
    def outcome_observed(self) -> bool:
        return self.sample_weight > 0.0 and any(
            value is not None
            for value in (
                self.high_return_pct,
                self.close_return_pct,
                self.upper_limit_touched,
            )
        )


@dataclass(frozen=True)
class ObservedRate:
    metric: str
    numerator: int
    denominator: int
    weighted_numerator: float
    weighted_denominator: float
    observed_rate: float | None
    lower_bound: float | None
    upper_bound: float | None
    bootstrap_iterations: int


def project_population_record(record: BrainRecordEnvelope) -> PopulationRecordProjection:
    """Project only statistical fields; conflicts fail closed to missing."""

    mappings = _payload_mappings(record.payload)
    high_return, high_status = _consistent_float(mappings, _HIGH_RETURN_FIELDS)
    close_return, close_status = _consistent_float(mappings, _CLOSE_RETURN_FIELDS)
    upper_limit, upper_status = _consistent_bool(mappings, _UPPER_LIMIT_FIELDS)
    weight, weight_status = _sample_weight(mappings)
    declared_observed, declared_observed_present = _declared_bool(
        mappings,
        _OUTCOME_OBSERVED_FIELDS,
    )
    projected_observed = any(
        value is not None for value in (high_return, close_return, upper_limit)
    )
    if declared_observed_present and (
        declared_observed is None or declared_observed is not projected_observed
    ):
        high_return = None
        close_return = None
        upper_limit = None
        high_status = "INVALID_CONFLICT"
        close_status = "INVALID_CONFLICT"
        upper_status = "INVALID_CONFLICT"
        projected_observed = False
    return PopulationRecordProjection(
        path_type=_consistent_text(mappings, _PATH_FIELDS),
        regime_cluster=_consistent_text(mappings, _REGIME_FIELDS),
        high_return_pct=high_return,
        close_return_pct=close_return,
        upper_limit_touched=upper_limit,
        outcome_observed=projected_observed,
        sample_weight=weight,
        high_return_status=high_status,
        close_return_status=close_status,
        upper_limit_status=upper_status,
        sample_weight_status=weight_status,
    )


def effective_sample_size(weights: Iterable[float]) -> float:
    values = [value for value in weights if math.isfinite(value) and value > 0.0]
    if not values:
        return 0.0
    numerator = sum(values) ** 2
    denominator = sum(value * value for value in values)
    return min(float(len(values)), numerator / denominator)


def aggregate_unit_values(values: Iterable[float | None]) -> float | None:
    observed = [value for value in values if value is not None and math.isfinite(value)]
    if not observed:
        return None
    if max(observed) - min(observed) > 1e-9:
        return None
    return observed[0]


def aggregate_unit_bool(values: Iterable[bool | None]) -> bool | None:
    observed = {value for value in values if value is not None}
    if len(observed) != 1:
        return None
    return observed.pop()


def aggregate_unit_label(values: Iterable[str], *, missing: str) -> str:
    observed = {value for value in values if value}
    if not observed:
        return missing
    if len(observed) == 1:
        return observed.pop()
    return "CONFLICTING"


def observed_rate(
    units: list[UnitObservation],
    *,
    metric: str,
    seed: int,
    bootstrap_iterations: int = DEFAULT_BOOTSTRAP_ITERATIONS,
) -> ObservedRate:
    predicates: dict[
        str,
        tuple[
            Callable[[UnitObservation], bool],
            Callable[[UnitObservation], bool],
        ],
    ] = {
        "upper_limit_touched": (
            lambda unit: unit.upper_limit_touched is not None,
            lambda unit: unit.upper_limit_touched is True,
        ),
        "high_return_5": (
            lambda unit: unit.high_return_pct is not None,
            lambda unit: unit.high_return_pct is not None
            and unit.high_return_pct >= 5.0,
        ),
        "high_return_10": (
            lambda unit: unit.high_return_pct is not None,
            lambda unit: unit.high_return_pct is not None
            and unit.high_return_pct >= 10.0,
        ),
        "high_return_20": (
            lambda unit: unit.high_return_pct is not None,
            lambda unit: unit.high_return_pct is not None
            and unit.high_return_pct >= 20.0,
        ),
    }
    if metric not in predicates:
        raise ValueError(f"unsupported observed population metric: {metric}")
    observed_predicate, positive_predicate = predicates[metric]
    observed = [
        unit
        for unit in units
        if unit.sample_weight > 0.0 and observed_predicate(unit)
    ]
    numerator = sum(1 for unit in observed if positive_predicate(unit))
    denominator = len(observed)
    weighted_numerator = sum(
        unit.sample_weight for unit in observed if positive_predicate(unit)
    )
    weighted_denominator = sum(unit.sample_weight for unit in observed)
    rate = (
        weighted_numerator / weighted_denominator if weighted_denominator else None
    )
    lower, upper = _trade_date_block_interval(
        observed,
        predicate=positive_predicate,
        seed=seed,
        iterations=bootstrap_iterations,
    )
    return ObservedRate(
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        weighted_numerator=weighted_numerator,
        weighted_denominator=weighted_denominator,
        observed_rate=rate,
        lower_bound=lower,
        upper_bound=upper,
        bootstrap_iterations=bootstrap_iterations,
    )


def time_slices(trade_date: date, *, cutoff_date: date) -> tuple[str, ...]:
    age_days = (cutoff_date - trade_date).days
    slices = ["ALL_HISTORY"]
    if age_days < 0:
        raise ValueError("population member trade date is after the analysis cutoff")
    if age_days <= 365:
        slices.append("RECENT_1Y")
    if age_days <= 3 * 365:
        slices.append("RECENT_3Y")
    elif age_days <= 10 * 365:
        slices.append("HISTORICAL_3_TO_10Y")
    else:
        slices.append("OLDER_THAN_10Y")
    return tuple(slices)


def _trade_date_block_interval(
    units: list[UnitObservation],
    *,
    predicate: Any,
    seed: int,
    iterations: int,
) -> tuple[float | None, float | None]:
    if not units or iterations < 1:
        return None, None
    blocks: dict[date, list[UnitObservation]] = {}
    for unit in units:
        blocks.setdefault(unit.trade_date, []).append(unit)
    days = sorted(blocks)
    block_weights = {
        day: (
            sum(unit.sample_weight for unit in blocks[day] if predicate(unit)),
            sum(unit.sample_weight for unit in blocks[day]),
        )
        for day in days
    }
    rng = random.Random(seed)
    rates: list[float] = []
    for _ in range(iterations):
        sampled = [rng.choice(days) for _day in days]
        numerator = sum(block_weights[day][0] for day in sampled)
        denominator = sum(block_weights[day][1] for day in sampled)
        if denominator:
            rates.append(numerator / denominator)
    if not rates:
        return None, None
    rates.sort()
    return _percentile(rates, 0.025), _percentile(rates, 0.975)


def _percentile(values: list[float], quantile: float) -> float:
    if len(values) == 1:
        return values[0]
    position = quantile * (len(values) - 1)
    lower_index = math.floor(position)
    upper_index = math.ceil(position)
    if lower_index == upper_index:
        return values[lower_index]
    fraction = position - lower_index
    return values[lower_index] * (1.0 - fraction) + values[upper_index] * fraction


def _payload_mappings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    queue = [payload]
    seen: set[int] = set()
    while queue:
        mapping = queue.pop(0)
        if id(mapping) in seen:
            continue
        seen.add(id(mapping))
        mappings.append(mapping)
        for nested in mapping.values():
            if isinstance(nested, dict):
                queue.append(nested)
            elif isinstance(nested, list):
                queue.extend(item for item in nested if isinstance(item, dict))
    return mappings


def _consistent_float(
    mappings: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> tuple[float | None, str]:
    values: list[float] = []
    invalid_present = False
    for mapping in mappings:
        for field in fields:
            if field not in mapping or mapping[field] is None:
                continue
            value = _finite_float(mapping[field])
            if value is None:
                invalid_present = True
            else:
                values.append(value)
    if invalid_present:
        return None, "INVALID_CONFLICT"
    if not values:
        return None, "MISSING"
    if max(values) - min(values) > 1e-9:
        return None, "INVALID_CONFLICT"
    return values[0], "VALID"


def _consistent_bool(
    mappings: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> tuple[bool | None, str]:
    values: list[bool] = []
    invalid_present = False
    for mapping in mappings:
        for field in fields:
            if field not in mapping or mapping[field] is None:
                continue
            value = _bool_value(mapping[field])
            if value is None:
                invalid_present = True
            else:
                values.append(value)
    if invalid_present or (values and len(set(values)) != 1):
        return None, "INVALID_CONFLICT"
    if not values:
        return None, "MISSING"
    return values[0], "VALID"


def _consistent_text(
    mappings: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> str:
    values = {
        value.strip().upper()
        for mapping in mappings
        for field in fields
        if isinstance((value := mapping.get(field)), str) and value.strip()
    }
    if not values:
        return "UNKNOWN"
    if len(values) > 1:
        return "CONFLICTING"
    return values.pop()


def _sample_weight(mappings: list[dict[str, Any]]) -> tuple[float, str]:
    raw_values = [
        mapping[field]
        for mapping in mappings
        for field in _WEIGHT_FIELDS
        if field in mapping
    ]
    if not raw_values:
        return 1.0, "DEFAULT"
    values = [_finite_float(value) for value in raw_values]
    if any(value is None or value <= 0.0 for value in values):
        return 0.0, "INVALID_CONFLICT"
    valid = [value for value in values if value is not None]
    if not math.isclose(
        max(valid),
        min(valid),
        rel_tol=1e-5,
        abs_tol=1e-5,
    ):
        return 0.0, "INVALID_CONFLICT"
    return min(valid), "VALID"


def _declared_bool(
    mappings: list[dict[str, Any]],
    fields: tuple[str, ...],
) -> tuple[bool | None, bool]:
    raw_values = [
        mapping[field]
        for mapping in mappings
        for field in fields
        if field in mapping
    ]
    if not raw_values:
        return None, False
    values = [_bool_value(value) for value in raw_values]
    if any(value is None for value in values) or len(set(values)) != 1:
        return None, True
    return values[0], True


def _finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = (
            float(value.strip().replace("%", ""))
            if isinstance(value, str)
            else float(value)
            if isinstance(value, int | float)
            else math.nan
        )
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _bool_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "touched", "hit"}:
            return True
        if normalized in {"false", "no", "0", "not_touched", "none"}:
            return False
    return None


def count_labels(units: list[UnitObservation], field: str) -> dict[str, int]:
    return dict(sorted(Counter(str(getattr(unit, field)) for unit in units).items()))
