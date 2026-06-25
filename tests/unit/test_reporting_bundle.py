from __future__ import annotations

import json
from datetime import date, datetime

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import BlindAnalysis, BlindPrediction
from news_scalping_lab.reporting.bundle import export_analysis_bundle
from news_scalping_lab.research_import.bundle import parse_bundle
from news_scalping_lab.utils import KST, canonical_json, sha256_text, write_json


def test_export_analysis_bundle_writes_single_markdown_bundle(tmp_path) -> None:
    settings = Settings(project_root=tmp_path)
    run_id = "RUN-bundle"
    trade_date = date(2030, 1, 10)
    cutoff_at = datetime(2030, 1, 10, 8, 59, 59, tzinfo=KST)
    prediction = BlindPrediction(
        prediction_id="PRED-bundle",
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        created_at=cutoff_at,
        sealed_at=cutoff_at,
        blind_analysis=BlindAnalysis(summary="blind summary"),
    )
    blind_hash = sha256_text(canonical_json(prediction.model_dump(mode="json")))
    prediction = prediction.model_copy(update={"blind_artifact_sha256": blind_hash})
    prediction_path = tmp_path / "predictions" / f"{trade_date.isoformat()}.json"
    write_json(prediction_path, prediction.model_dump(mode="json"))
    report_path = tmp_path / "reports" / f"{trade_date.isoformat()}_preopen.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# preopen report\n", encoding="utf-8")
    row_disposition = canonical_json(
        {
            "row_number": 1,
            "event_id": "EVT-1",
            "disposition": "INCLUDED_BEFORE_CUTOFF",
        }
    ) + "\n"
    row_path = tmp_path / "runs" / "checkpoints" / "row_disposition" / run_id / "row_disposition.jsonl"
    row_path.parent.mkdir(parents=True)
    row_path.write_text(row_disposition, encoding="utf-8")
    source_ledger = canonical_json(
        {
            "source_id": "SRC-1",
            "source_type": "news_csv_row",
            "title": "source title",
            "publisher": None,
            "url": "news://EVT-1",
            "published_at": cutoff_at.isoformat(),
            "retrieved_at": cutoff_at.isoformat(),
            "time_verified": True,
            "available_before_cutoff": True,
            "usage_phase": "BLIND",
            "input_row_ids": [1],
            "content_sha256": "abc",
            "notes": "test source",
        }
    ) + "\n"
    source_path = tmp_path / "runs" / "checkpoints" / "source_ledger" / run_id / "source_ledger.jsonl"
    source_path.parent.mkdir(parents=True)
    source_path.write_text(source_ledger, encoding="utf-8")
    receipt = {
        "schema_version": "nslab.blind_seal_receipt.v1",
        "run_id": run_id,
        "prediction_id": prediction.prediction_id,
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "sealed_at": cutoff_at.isoformat(),
        "phase": "BLIND_SEALED",
        "blind_context_mode": "NEWS_ONLY_STRICT",
        "blind_artifact_sha256": blind_hash,
        "blind_prediction_path": prediction_path.relative_to(tmp_path).as_posix(),
        "row_disposition_sha256": sha256_text(row_disposition),
        "row_disposition_coverage_ratio": 1.0,
        "source_ledger_sha256": sha256_text(source_ledger),
        "no_d_outcome_exposed": True,
        "validation": {"canonical_blind_hash_verified": True},
    }
    receipt_path = (
        tmp_path
        / "runs"
        / "checkpoints"
        / "blind_seal"
        / run_id
        / "blind_seal_receipt.json"
    )
    write_json(receipt_path, receipt)
    phase_state = {
        "schema_version": "nslab.phase_state.v1",
        "run_id": run_id,
        "phase": "BLIND_SEALED",
        "completed_phases": ["PHASE_A_NEWS_ONLY_BLIND"],
        "trade_date": trade_date.isoformat(),
        "cutoff_at": cutoff_at.isoformat(),
        "sealed_at": cutoff_at.isoformat(),
        "blind_seal_receipt_sha256": sha256_text(receipt_path.read_text(encoding="utf-8")),
    }
    phase_path = tmp_path / "runs" / "checkpoints" / "phase_state" / run_id / "phase_state.json"
    write_json(phase_path, phase_state)
    write_json(
        tmp_path / "runs" / "manifests" / f"{run_id}.json",
        {
            "run_id": run_id,
            "trade_date": trade_date.isoformat(),
            "cutoff_at": cutoff_at.isoformat(),
            "blind_context_mode": "NEWS_ONLY_STRICT",
            "blind_web_search_call_count": 0,
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "blind_artifact_sha256": blind_hash,
            "blind_seal_receipt_artifact": receipt_path.relative_to(tmp_path).as_posix(),
            "blind_seal_receipt_sha256": sha256_text(
                receipt_path.read_text(encoding="utf-8")
            ),
            "phase_state_artifact": phase_path.relative_to(tmp_path).as_posix(),
            "phase_state_sha256": sha256_text(phase_path.read_text(encoding="utf-8")),
            "price_snapshot": {"source_name": "mock", "allowed_through": "2030-01-09"},
            "row_disposition_artifact": row_path.relative_to(tmp_path).as_posix(),
            "row_disposition_sha256": sha256_text(row_disposition),
            "row_disposition_coverage_ratio": 1.0,
            "source_ledger_artifact": source_path.relative_to(tmp_path).as_posix(),
            "source_ledger_sha256": sha256_text(source_ledger),
            "source_ledger_entry_count": 1,
        },
    )

    output = export_analysis_bundle(settings, run_id=run_id)
    parsed = parse_bundle(output)

    assert output.name == "20300110_nslab_episode_bundle.md"
    assert set(parsed.blocks) == {
        "research_report.md",
        "blind_prediction.json",
        "research_episode.json",
        "row_disposition.jsonl",
        "brain_delta.jsonl",
        "source_ledger.jsonl",
        "bundle_manifest.json",
    }
    assert parsed.validation["blind_hash_verified"]
    assert parsed.validation["row_disposition_hash_verified"]
    assert parsed.validation["source_ledger_hash_verified"]
    assert parsed.validation["research_episode_hash_verified"]
    assert parsed.validation["brain_delta_hash_verified"]
    assert parsed.validation["blind_seal_receipt_hash_verified"]
    manifest = parsed.json_blocks["bundle_manifest.json"]
    assert isinstance(manifest, dict)
    assert manifest["bundle_incomplete"] is True
    assert manifest["blind_seal_receipt_sha256"]
    assert manifest["validation"]["research_episode_hash_verified"] is True
    assert manifest["validation"]["brain_delta_hash_verified"] is True
    episode = parsed.json_blocks["research_episode.json"]
    assert isinstance(episode, dict)
    assert episode["blind_seal_receipt"]["phase"] == "BLIND_SEALED"
    assert parsed.jsonl_blocks["brain_delta.jsonl"][0]["record_type"] == "bundle_incomplete"
    assert json.loads(parsed.blocks["row_disposition.jsonl"].splitlines()[1])["row_number"] == 1
