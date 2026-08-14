"""Isolated post-close web audit that cannot modify a sealed BLIND prediction."""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from news_scalping_lab.utils import KST, file_sha256, now_kst, write_json
from news_scalping_lab.web.provider import TemporalWebGuard, WebResearchProvider


async def run_postclose_web_audit(
    root: Path,
    *,
    trade_date: date,
    queries: list[str],
    provider: WebResearchProvider,
    available_from: datetime | None = None,
) -> tuple[dict[str, Any], Path]:
    prediction_path = root / "predictions" / f"{trade_date.isoformat()}.json"
    if not prediction_path.is_file():
        raise FileNotFoundError(f"sealed BLIND prediction is missing: {prediction_path}")
    audit_at = available_from or now_kst()
    close_at = datetime.combine(trade_date, time(15, 30), tzinfo=KST)
    if audit_at < close_at:
        raise ValueError("post-close web audit cannot run before market close")
    before_sha256 = file_sha256(prediction_path)
    guard = TemporalWebGuard(provider)
    rows: list[dict[str, Any]] = []
    for query in queries:
        for result in await guard.search(query, cutoff_at=audit_at):
            rows.append(
                {
                    "source_id": result.source_id,
                    "query": query,
                    "title": result.title,
                    "url": result.url,
                    "published_at": (
                        result.published_at.isoformat()
                        if result.published_at is not None
                        else None
                    ),
                    "available_from": audit_at.isoformat(),
                }
            )
    after_sha256 = file_sha256(prediction_path)
    if after_sha256 != before_sha256:
        raise RuntimeError("post-close audit observed BLIND prediction mutation")
    artifact = {
        "schema_version": "nslab.postclose_web_audit.v1",
        "trade_date": trade_date.isoformat(),
        "available_from": audit_at.isoformat(),
        "evidence_policy": "postclose-web-audit-optional",
        "prediction_path": prediction_path.relative_to(root).as_posix(),
        "prediction_sha256": before_sha256,
        "query_count": len(queries),
        "source_count": len(rows),
        "sources": rows,
        "blind_prediction_mutated": False,
        "production_candidate_rank_mutated": False,
        "training_record_created": False,
    }
    path = (
        root
        / "runs"
        / "postclose_web_audit"
        / trade_date.isoformat()
        / "audit.json"
    )
    write_json(path, artifact)
    return artifact, path
