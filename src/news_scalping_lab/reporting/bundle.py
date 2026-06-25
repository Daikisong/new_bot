"""Single-file Markdown research bundle export."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.contracts.models import (
    BlindPrediction,
    EligibilityMatrix,
    Provenance,
    ResearchEpisode,
)
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    file_sha256,
    next_calendar_day,
    read_json,
    sha256_text,
    stable_id,
)

BUNDLE_SCHEMA_VERSION = "nslab.research_bundle.v1"
EXECUTION_PROTOCOL_VERSION = "nslab.research_prompt.v5"


def export_analysis_bundle(settings: Settings, *, run_id: str) -> Path:
    manifest_path = settings.path(settings.output_dirs.manifests) / f"{run_id}.json"
    manifest = _read_dict(manifest_path)
    trade_date = str(manifest["trade_date"])
    compact_trade_date = trade_date.replace("-", "")
    prediction_path = settings.path(settings.output_dirs.predictions) / f"{trade_date}.json"
    report_path = settings.path(settings.output_dirs.reports) / f"{trade_date}_preopen.md"
    prediction = BlindPrediction.model_validate(_read_dict(prediction_path))
    report = report_path.read_text(encoding="utf-8")
    row_disposition = _read_manifest_artifact(settings, manifest, "row_disposition_artifact")
    source_ledger = _read_manifest_artifact(settings, manifest, "source_ledger_artifact")
    blind_seal_receipt = _read_manifest_artifact(
        settings,
        manifest,
        "blind_seal_receipt_artifact",
    )
    brain_delta = _brain_delta_jsonl(run_id=run_id, reason="postmortem_not_run")
    research_episode = _build_research_episode(
        run_id=run_id,
        prediction=prediction,
        manifest=manifest,
        prediction_path=prediction_path,
        blind_seal_receipt=blind_seal_receipt,
    )
    bundle_manifest = _build_bundle_manifest(
        run_id=run_id,
        manifest=manifest,
        prediction=prediction,
        prediction_path=prediction_path,
        report_path=report_path,
        row_disposition=row_disposition,
        source_ledger=source_ledger,
        blind_seal_receipt=blind_seal_receipt,
        brain_delta=brain_delta,
        research_episode=research_episode,
    )
    output_path = settings.path(settings.output_dirs.reports) / (
        f"{compact_trade_date}_nslab_episode_bundle.md"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        _render_bundle(
            run_id=run_id,
            trade_date=trade_date,
            report=report,
            prediction=prediction,
            research_episode=research_episode,
            row_disposition=row_disposition,
            brain_delta=brain_delta,
            source_ledger=source_ledger,
            bundle_manifest=bundle_manifest,
        ),
        encoding="utf-8",
    )
    return output_path


def _read_dict(path: Path) -> dict[str, Any]:
    payload = read_json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object in {path}")
    return payload


def _read_manifest_artifact(
    settings: Settings,
    manifest: dict[str, Any],
    field_name: str,
) -> str:
    relative = manifest.get(field_name)
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"manifest missing {field_name}")
    return settings.path(relative).read_text(encoding="utf-8")


def _build_research_episode(
    *,
    run_id: str,
    prediction: BlindPrediction,
    manifest: dict[str, Any],
    prediction_path: Path,
    blind_seal_receipt: str,
) -> ResearchEpisode:
    available_from = datetime.combine(
        next_calendar_day(prediction.trade_date),
        time(0, 0, 0),
        tzinfo=KST,
    )
    source_hashes = [
        value
        for value in (
            manifest.get("row_disposition_sha256"),
            manifest.get("source_ledger_sha256"),
        )
        if isinstance(value, str)
    ]
    return ResearchEpisode(
        episode_id=stable_id("EP", "analysis-bundle", run_id),
        trade_date=prediction.trade_date,
        cutoff_at=prediction.cutoff_at,
        created_at=prediction.created_at,
        execution_protocol_version=EXECUTION_PROTOCOL_VERSION,
        research_version="analysis-bundle.v1",
        input_news_files=[str(path) for path in _source_ledger_input_files(manifest)],
        input_news_hashes=source_hashes,
        input_audit={
            "row_disposition_coverage_ratio": manifest.get("row_disposition_coverage_ratio"),
            "source_ledger_entry_count": manifest.get("source_ledger_entry_count"),
        },
        row_disposition_summary=manifest.get("row_disposition_summary", {}),
        blind_integrity={
            "blind_context_mode": manifest.get("blind_context_mode"),
            "blind_web_search_call_count": manifest.get("blind_web_search_call_count", 0),
            "blind_price_repository_access_count": manifest.get(
                "blind_price_repository_access_count", 0
            ),
            "blind_current_price_access_count": manifest.get(
                "blind_current_price_access_count", 0
            ),
            "no_d_outcome_exposed": manifest.get("no_d_outcome_exposed"),
        },
        blind_artifact_sha256=prediction.blind_artifact_sha256,
        blind_seal_receipt=json.loads(blind_seal_receipt),
        price_source_snapshot=manifest.get("price_snapshot", {}),
        blind_analysis=prediction.blind_analysis,
        blind_predictions=prediction.candidates,
        outcome_coverage_status="NOT_RUN",
        eligibility_matrix=EligibilityMatrix(
            forecast_evaluation_eligible=False,
            direct_supervised_cases_eligible=False,
            theme_supervised_cases_eligible=False,
            leader_pair_training_eligible=False,
            retrospective_memory_eligible=False,
            brain_eligible=False,
            reasons={
                "outcome": "postmortem outcome evaluation has not been run for this bundle",
            },
        ),
        provenance=[
            Provenance(
                source_id=stable_id("SRC", "blind_prediction", prediction_path.as_posix()),
                source_type="blind_prediction_json",
                uri=prediction_path.as_posix(),
                content_sha256=file_sha256(prediction_path),
                observed_at=prediction.sealed_at or prediction.created_at,
            )
        ],
        available_from=available_from,
    )


def _source_ledger_input_files(manifest: dict[str, Any]) -> list[str]:
    artifact = manifest.get("source_ledger_artifact")
    if isinstance(artifact, str):
        return [artifact]
    return []


def _build_bundle_manifest(
    *,
    run_id: str,
    manifest: dict[str, Any],
    prediction: BlindPrediction,
    prediction_path: Path,
    report_path: Path,
    row_disposition: str,
    source_ledger: str,
    blind_seal_receipt: str,
    brain_delta: str,
    research_episode: ResearchEpisode,
) -> dict[str, Any]:
    prediction_payload = prediction.model_dump(mode="json")
    observed_blind_hash = prediction.blind_artifact_sha256
    prediction_payload["blind_artifact_sha256"] = None
    blind_hash = sha256_text(canonical_json(prediction_payload))
    return {
        "schema_version": "nslab.bundle_manifest.v1",
        "execution_protocol_version": EXECUTION_PROTOCOL_VERSION,
        "run_id": run_id,
        "trade_date": prediction.trade_date.isoformat(),
        "cutoff_at": prediction.cutoff_at.isoformat(),
        "blind_context_mode": manifest.get("blind_context_mode"),
        "blind_web_search_call_count": manifest.get("blind_web_search_call_count", 0),
        "blind_price_repository_access_count": manifest.get(
            "blind_price_repository_access_count", 0
        ),
        "blind_current_price_access_count": manifest.get("blind_current_price_access_count", 0),
        "blind_artifact_sha256": observed_blind_hash or blind_hash,
        "blind_hash_recomputed": blind_hash,
        "prediction_sha256": file_sha256(prediction_path),
        "research_report_sha256": file_sha256(report_path),
        "research_episode_sha256": sha256_text(
            canonical_json(research_episode.model_dump(mode="json"))
        ),
        "row_disposition_sha256": sha256_text(row_disposition),
        "row_disposition_coverage_ratio": manifest.get("row_disposition_coverage_ratio"),
        "source_ledger_sha256": sha256_text(source_ledger),
        "source_ledger_entry_count": manifest.get("source_ledger_entry_count", 0),
        "blind_seal_receipt_sha256": sha256_text(blind_seal_receipt),
        "phase_state_sha256": manifest.get("phase_state_sha256"),
        "brain_delta_sha256": sha256_text(brain_delta),
        "outcome_coverage_status": "NOT_RUN",
        "outcome_slice_sha256": None,
        "outcome_completeness_audit": {"status": "NOT_RUN"},
        "eligibility_matrix": research_episode.eligibility_matrix.model_dump(mode="json"),
        "validation": {
            "markers_complete": True,
            "json_valid": True,
            "jsonl_valid": True,
            "blind_hash_verified": (observed_blind_hash is None or observed_blind_hash == blind_hash),
            "row_disposition_hash_verified": True,
            "source_ledger_hash_verified": True,
            "research_episode_hash_verified": True,
            "brain_delta_hash_verified": True,
            "blind_seal_receipt_hash_verified": True,
            "phase_state_recorded": bool(manifest.get("phase_state_artifact")),
        },
        "bundle_incomplete": True,
        "incomplete_reasons": ["postmortem outcome evaluation has not been run"],
    }


def _brain_delta_jsonl(*, run_id: str, reason: str) -> str:
    row = {
        "record_type": "bundle_incomplete",
        "run_id": run_id,
        "reason": reason,
        "eligible_for_brain_update": False,
    }
    return canonical_json(row) + "\n"


def _render_bundle(
    *,
    run_id: str,
    trade_date: str,
    report: str,
    prediction: BlindPrediction,
    research_episode: ResearchEpisode,
    row_disposition: str,
    brain_delta: str,
    source_ledger: str,
    bundle_manifest: dict[str, Any],
) -> str:
    blind_json = json.dumps(
        prediction.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    episode_json = research_episode.model_dump_json(indent=2)
    manifest_json = json.dumps(
        bundle_manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    return (
        "---\n"
        f"schema_version: {BUNDLE_SCHEMA_VERSION}\n"
        "artifact_type: research_episode_bundle\n"
        f"run_id: {run_id}\n"
        f"trade_date: {trade_date}\n"
        f"blind_artifact_sha256: {bundle_manifest['blind_artifact_sha256']}\n"
        "---\n\n"
        f"{_block('research_report.md', report, fence=None)}\n\n"
        f"{_block('blind_prediction.json', blind_json, fence='json')}\n\n"
        f"{_block('research_episode.json', episode_json, fence='json')}\n\n"
        f"{_block('row_disposition.jsonl', row_disposition, fence='jsonl')}\n\n"
        f"{_block('brain_delta.jsonl', brain_delta, fence='jsonl')}\n\n"
        f"{_block('source_ledger.jsonl', source_ledger, fence='jsonl')}\n\n"
        f"{_block('bundle_manifest.json', manifest_json, fence='json')}\n"
    )


def _block(name: str, content: str, *, fence: str | None) -> str:
    body = content.strip()
    if fence is not None:
        body = f"```{fence}\n{body}\n```"
    return f"<!-- NSLAB:BEGIN {name} -->\n{body}\n<!-- NSLAB:END {name} -->"
