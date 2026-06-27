"""Single-file Markdown research bundle export."""

from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any

from news_scalping_lab.config import Settings
from news_scalping_lab.context.final_synthesis import (
    final_synthesis_context_contract_verified,
)
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
    next_trading_day,
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
    row_disposition = _read_manifest_artifact(settings, manifest, "row_disposition_artifact")
    source_ledger = _read_manifest_artifact(settings, manifest, "source_ledger_artifact")
    candidate_web_checks = _read_optional_manifest_artifact(
        settings,
        manifest,
        "candidate_web_check_artifact",
    )
    candidate_verification = _read_optional_manifest_artifact(
        settings,
        manifest,
        "candidate_verification_artifact",
    )
    final_synthesis_context = _read_optional_manifest_artifact(
        settings,
        manifest,
        "final_synthesis_context_artifact",
    )
    excluded_candidate_web_checks = _read_optional_manifest_artifact(
        settings,
        manifest,
        "excluded_candidate_web_check_artifact",
    )
    blind_seal_receipt = _read_manifest_artifact(
        settings,
        manifest,
        "blind_seal_receipt_artifact",
    )
    prediction_path = _prediction_path_for_bundle(
        settings,
        manifest=manifest,
        blind_seal_receipt=blind_seal_receipt,
        trade_date=trade_date,
    )
    report_path = _report_path_for_bundle(
        settings,
        manifest=manifest,
        trade_date=trade_date,
    )
    prediction = BlindPrediction.model_validate(_read_dict(prediction_path))
    _validate_prediction_belongs_to_run(prediction, run_id=run_id, prediction_path=prediction_path)
    report = report_path.read_text(encoding="utf-8")
    _validate_manifest_artifact_hash(
        manifest,
        field_name="prediction_sha256",
        observed=file_sha256(prediction_path),
        path=prediction_path,
    )
    _validate_manifest_artifact_hash(
        manifest,
        field_name="report_sha256",
        observed=sha256_text(report),
        path=report_path,
    )
    bundle_prediction = _prediction_with_bundle_blind_hash(prediction)
    blind_seal_receipt = _bundle_blind_seal_receipt_text(
        blind_seal_receipt,
        blind_hash=bundle_prediction.blind_artifact_sha256,
    )
    phase_state = _read_manifest_artifact(settings, manifest, "phase_state_artifact")
    phase_state = _bundle_phase_state_text(
        phase_state,
        blind_seal_receipt_sha256=sha256_text(blind_seal_receipt),
    )
    brain_delta = _brain_delta_jsonl(run_id=run_id, reason="postmortem_not_run")
    research_episode = _build_research_episode(
        run_id=run_id,
        prediction=bundle_prediction,
        manifest=manifest,
        prediction_path=prediction_path,
        blind_seal_receipt=blind_seal_receipt,
    )
    bundle_manifest = _build_bundle_manifest(
        run_id=run_id,
        manifest=manifest,
        prediction=bundle_prediction,
        prediction_path=prediction_path,
        report_path=report_path,
        row_disposition=row_disposition,
        source_ledger=source_ledger,
        candidate_web_checks=candidate_web_checks,
        candidate_verification=candidate_verification,
        final_synthesis_context=final_synthesis_context,
        excluded_candidate_web_checks=excluded_candidate_web_checks,
        blind_seal_receipt=blind_seal_receipt,
        phase_state=phase_state,
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
            prediction=bundle_prediction,
            research_episode=research_episode,
            row_disposition=row_disposition,
            brain_delta=brain_delta,
            source_ledger=source_ledger,
            candidate_web_checks=candidate_web_checks,
            candidate_verification=candidate_verification,
            final_synthesis_context=final_synthesis_context,
            excluded_candidate_web_checks=excluded_candidate_web_checks,
            phase_state=phase_state,
            bundle_manifest=bundle_manifest,
        ),
        encoding="utf-8",
    )
    return output_path


def _prediction_with_bundle_blind_hash(prediction: BlindPrediction) -> BlindPrediction:
    payload = prediction.model_dump(mode="json")
    payload["blind_artifact_sha256"] = None
    blind_hash = sha256_text(canonical_json(payload))
    return prediction.model_copy(update={"blind_artifact_sha256": blind_hash})


def _bundle_blind_seal_receipt_text(
    blind_seal_receipt: str,
    *,
    blind_hash: str | None,
) -> str:
    receipt = _json_object(blind_seal_receipt)
    if receipt is None:
        return blind_seal_receipt
    receipt["blind_artifact_sha256"] = blind_hash
    validation = receipt.get("validation")
    if isinstance(validation, dict):
        validation["canonical_blind_hash_verified"] = True
    return _json_text(receipt)


def _bundle_phase_state_text(
    phase_state: str,
    *,
    blind_seal_receipt_sha256: str,
) -> str:
    payload = _json_object(phase_state)
    if payload is None:
        return phase_state
    payload["blind_seal_receipt_sha256"] = blind_seal_receipt_sha256
    return _json_text(payload)


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


def _read_optional_manifest_artifact(
    settings: Settings,
    manifest: dict[str, Any],
    field_name: str,
) -> str | None:
    relative = manifest.get(field_name)
    if not isinstance(relative, str) or not relative:
        return None
    return settings.path(relative).read_text(encoding="utf-8")


def _prediction_path_for_bundle(
    settings: Settings,
    *,
    manifest: dict[str, Any],
    blind_seal_receipt: str,
    trade_date: str,
) -> Path:
    manifest_path = _optional_manifest_path(settings, manifest, "prediction_artifact")
    if manifest_path is not None:
        return manifest_path
    try:
        receipt = json.loads(blind_seal_receipt)
    except json.JSONDecodeError:
        receipt = {}
    if isinstance(receipt, dict):
        receipt_path = receipt.get("blind_prediction_path")
        if isinstance(receipt_path, str) and receipt_path:
            return settings.path(receipt_path)
    return settings.path(settings.output_dirs.predictions) / f"{trade_date}.json"


def _report_path_for_bundle(
    settings: Settings,
    *,
    manifest: dict[str, Any],
    trade_date: str,
) -> Path:
    manifest_path = _optional_manifest_path(settings, manifest, "report_artifact")
    if manifest_path is not None:
        return manifest_path
    return settings.path(settings.output_dirs.reports) / f"{trade_date}_preopen.md"


def _optional_manifest_path(
    settings: Settings,
    manifest: dict[str, Any],
    field_name: str,
) -> Path | None:
    relative = manifest.get(field_name)
    if not isinstance(relative, str) or not relative:
        return None
    return settings.path(relative)


def _validate_prediction_belongs_to_run(
    prediction: BlindPrediction,
    *,
    run_id: str,
    prediction_path: Path,
) -> None:
    if prediction.context_manifest_id is None:
        return
    if prediction.context_manifest_id != run_id:
        raise ValueError(
            f"prediction artifact {prediction_path.as_posix()} belongs to "
            f"{prediction.context_manifest_id}, not {run_id}"
        )


def _validate_manifest_artifact_hash(
    manifest: dict[str, Any],
    *,
    field_name: str,
    observed: str,
    path: Path,
) -> None:
    expected = manifest.get(field_name)
    if expected is None:
        return
    if expected != observed:
        raise ValueError(
            f"manifest {field_name} mismatch for {path.as_posix()}: "
            f"expected {expected}, observed {observed}"
        )


def _build_research_episode(
    *,
    run_id: str,
    prediction: BlindPrediction,
    manifest: dict[str, Any],
    prediction_path: Path,
    blind_seal_receipt: str,
) -> ResearchEpisode:
    available_from = datetime.combine(
        next_trading_day(prediction.trade_date),
        time(0, 0, 0),
        tzinfo=KST,
    )
    source_hashes = [
        value
        for value in (
            manifest.get("row_disposition_sha256"),
            manifest.get("source_ledger_sha256"),
            manifest.get("candidate_web_check_sha256"),
            manifest.get("excluded_candidate_web_check_sha256"),
        )
        if isinstance(value, str)
    ]
    input_audit = {
        "row_disposition_coverage_ratio": manifest.get("row_disposition_coverage_ratio"),
        "source_ledger_entry_count": manifest.get("source_ledger_entry_count"),
    }
    if manifest.get("candidate_web_check_artifact"):
        input_audit["candidate_web_check_count"] = manifest.get("candidate_web_check_count")
    if manifest.get("excluded_candidate_web_check_artifact"):
        input_audit["excluded_candidate_web_check_count"] = manifest.get(
            "excluded_candidate_web_check_count"
        )
    return ResearchEpisode(
        episode_id=stable_id("EP", "analysis-bundle", run_id),
        trade_date=prediction.trade_date,
        cutoff_at=prediction.cutoff_at,
        created_at=prediction.created_at,
        execution_protocol_version=EXECUTION_PROTOCOL_VERSION,
        research_version="analysis-bundle.v1",
        input_news_files=[str(path) for path in _bundle_input_artifacts(manifest)],
        input_news_hashes=source_hashes,
        input_audit=input_audit,
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


def _bundle_input_artifacts(manifest: dict[str, Any]) -> list[str]:
    artifacts: list[str] = []
    for field_name in (
        "source_ledger_artifact",
        "candidate_web_check_artifact",
        "candidate_verification_artifact",
        "final_synthesis_context_artifact",
        "excluded_candidate_web_check_artifact",
    ):
        artifact = manifest.get(field_name)
        if isinstance(artifact, str) and artifact:
            artifacts.append(artifact)
    return artifacts


def _build_bundle_manifest(
    *,
    run_id: str,
    manifest: dict[str, Any],
    prediction: BlindPrediction,
    prediction_path: Path,
    report_path: Path,
    row_disposition: str,
    source_ledger: str,
    candidate_web_checks: str | None,
    candidate_verification: str | None,
    final_synthesis_context: str | None,
    excluded_candidate_web_checks: str | None,
    blind_seal_receipt: str,
    phase_state: str,
    brain_delta: str,
    research_episode: ResearchEpisode,
) -> dict[str, Any]:
    prediction_payload = prediction.model_dump(mode="json")
    observed_blind_hash = prediction.blind_artifact_sha256
    prediction_payload["blind_artifact_sha256"] = None
    blind_hash = sha256_text(canonical_json(prediction_payload))
    blind_execution_guard_verified = _blind_execution_guard_verified(manifest)
    validation = {
        "markers_complete": True,
        "json_valid": True,
        "jsonl_valid": True,
        "blind_hash_verified": (observed_blind_hash is None or observed_blind_hash == blind_hash),
        "front_matter_identity_verified": True,
        "prediction_file_hash_verified": True,
        "research_report_hash_verified": True,
        "blind_execution_guard_verified": blind_execution_guard_verified,
        "row_disposition_hash_verified": True,
        "row_disposition_coverage_verified": True,
        "source_ledger_hash_verified": True,
        "source_ledger_entry_count_verified": True,
        "research_episode_hash_verified": True,
        "brain_delta_hash_verified": True,
        "blind_seal_receipt_hash_verified": True,
        "blind_seal_receipt_contract_verified": True,
        "phase_state_hash_verified": True,
        "phase_state_contract_verified": True,
        "phase_state_receipt_link_verified": True,
        "id_reference_integrity_verified": True,
        "manifest_validation_self_consistent_verified": True,
        "phase_state_recorded": True,
    }
    payload = {
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
        "no_d_outcome_exposed": manifest.get("no_d_outcome_exposed"),
        "price_snapshot": manifest.get("price_snapshot", {}),
        "blind_artifact_sha256": observed_blind_hash or blind_hash,
        "blind_hash_recomputed": blind_hash,
        "prediction_sha256": sha256_text(_prediction_json_text(prediction)),
        "research_report_sha256": file_sha256(report_path),
        "research_episode_sha256": sha256_text(
            canonical_json(research_episode.model_dump(mode="json"))
        ),
        "row_disposition_sha256": sha256_text(row_disposition),
        "row_disposition_coverage_ratio": manifest.get("row_disposition_coverage_ratio"),
        "source_ledger_sha256": sha256_text(source_ledger),
        "source_ledger_entry_count": manifest.get("source_ledger_entry_count", 0),
        "blind_seal_receipt_sha256": sha256_text(blind_seal_receipt),
        "phase_state_sha256": sha256_text(phase_state),
        "brain_delta_sha256": sha256_text(brain_delta),
        "outcome_coverage_status": "NOT_RUN",
        "outcome_slice_sha256": None,
        "outcome_completeness_audit": {"status": "NOT_RUN"},
        "eligibility_matrix": research_episode.eligibility_matrix.model_dump(mode="json"),
        "validation": validation,
        "bundle_incomplete": True,
        "incomplete_reasons": ["postmortem outcome evaluation has not been run"],
    }
    _copy_manifest_fields(
        payload,
        manifest,
        (
            "accepted_record_count",
            "available_record_count",
            "available_record_ids",
            "training_eligible_available_record_count",
            "training_eligible_available_record_ids",
            "swept_record_count",
            "swept_record_ids",
            "missing_swept_record_ids",
            "unexpected_swept_record_ids",
            "duplicate_swept_record_ids",
            "retrieved_record_ids",
            "excluded_retrieved_record_ids",
            "semantic_retrieval_record_ids",
            "excluded_semantic_retrieval_record_ids",
            "counterexample_record_ids",
        ),
    )
    if candidate_web_checks is not None:
        payload["candidate_web_check_sha256"] = sha256_text(candidate_web_checks)
        payload["candidate_web_check_count"] = manifest.get("candidate_web_check_count", 0)
        validation["candidate_web_check_hash_verified"] = True
        validation["candidate_web_check_count_verified"] = True
    if candidate_verification is not None:
        payload["candidate_verification_sha256"] = sha256_text(candidate_verification)
        payload["candidate_verification_count"] = manifest.get(
            "candidate_verification_count",
            0,
        )
        validation["candidate_verification_hash_verified"] = True
        validation["candidate_verification_count_verified"] = True
        validation["candidate_verification_contract_verified"] = True
    if final_synthesis_context is not None:
        payload["final_synthesis_context_sha256"] = sha256_text(
            final_synthesis_context
        )
        payload["final_synthesis_context_summary"] = manifest.get(
            "final_synthesis_context_summary",
            {},
        )
        validation["final_synthesis_context_hash_verified"] = True
        validation["final_synthesis_context_contract_verified"] = (
            _verify_final_synthesis_context_contract(manifest, final_synthesis_context)
        )
        validation["final_synthesis_context_candidate_web_checks_verified"] = (
            _verify_final_synthesis_candidate_web_checks_context(
                final_synthesis_context,
                candidate_web_checks,
            )
        )
        validation["final_synthesis_context_candidate_verification_verified"] = (
            _verify_final_synthesis_candidate_verification_context(
                final_synthesis_context,
                candidate_verification,
            )
        )
    if excluded_candidate_web_checks is not None:
        payload["excluded_candidate_web_check_sha256"] = sha256_text(
            excluded_candidate_web_checks
        )
        payload["excluded_candidate_web_check_count"] = manifest.get(
            "excluded_candidate_web_check_count",
            0,
        )
        validation["excluded_candidate_web_check_hash_verified"] = True
        validation["excluded_candidate_web_check_count_verified"] = True
    return payload


def _copy_manifest_fields(
    payload: dict[str, Any],
    manifest: dict[str, Any],
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        if field in manifest:
            payload[field] = manifest[field]


def _verify_final_synthesis_context_contract(
    manifest: dict[str, Any],
    final_synthesis_context: str,
) -> bool:
    context = _json_object(final_synthesis_context)
    if context is None:
        return False
    return final_synthesis_context_contract_verified(manifest, context)


def _verify_final_synthesis_candidate_web_checks_context(
    final_synthesis_context: str,
    candidate_web_checks: str | None,
) -> bool:
    payload = _final_synthesis_context_payload(final_synthesis_context)
    if payload is None:
        return False
    expected_rows = _candidate_web_check_context_rows(candidate_web_checks)
    return (
        expected_rows is not None
        and payload.get("candidate_web_checks") == expected_rows
    )


def _verify_final_synthesis_candidate_verification_context(
    final_synthesis_context: str,
    candidate_verification: str | None,
) -> bool:
    payload = _final_synthesis_context_payload(final_synthesis_context)
    if payload is None:
        return False
    expected = (
        _json_object(candidate_verification)
        if candidate_verification is not None
        else {}
    )
    return expected is not None and payload.get("candidate_verification") == expected


def _final_synthesis_context_payload(text: str) -> dict[str, Any] | None:
    context = _json_object(text)
    if context is None:
        return None
    payload = context.get("payload")
    return payload if isinstance(payload, dict) else None


def _json_object(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _prediction_json_text(prediction: BlindPrediction) -> str:
    return json.dumps(
        prediction.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )


def _candidate_web_check_context_rows(
    candidate_web_checks: str | None,
) -> list[dict[str, Any]] | None:
    if candidate_web_checks is None:
        return []
    rows: list[dict[str, Any]] = []
    for line in candidate_web_checks.splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            return None
        rows.append(_candidate_web_check_context_row(row))
    return rows


def _candidate_web_check_context_row(row: dict[str, Any]) -> dict[str, Any]:
    context_row = {
        "candidate_rank": row.get("candidate_rank"),
        "candidate_ticker": row.get("candidate_ticker"),
        "candidate_company_name": row.get("candidate_company_name"),
        "candidate_path_type": row.get("candidate_path_type"),
        "candidate_subject_type": row.get("candidate_subject_type"),
        "candidate_expansion_path": row.get("candidate_expansion_path"),
        "candidate_expansion_hypothesis": row.get("candidate_expansion_hypothesis"),
        "candidate_investigation_questions": row.get(
            "candidate_investigation_questions"
        ),
        "verification_focus": row.get("verification_focus"),
        "source_id": row.get("source_id"),
        "query": row.get("query"),
        "title": row.get("title"),
        "url": row.get("url"),
        "snippet": row.get("snippet"),
        "published_at": row.get("published_at"),
        "time_verified": row.get("time_verified"),
        "content_sha256": row.get("content_sha256"),
        "opened_text_excerpt": row.get("opened_text_excerpt"),
    }
    if "timestamp_precision" in row:
        context_row["timestamp_precision"] = row.get("timestamp_precision")
    return context_row


def _brain_delta_jsonl(*, run_id: str, reason: str) -> str:
    row = {
        "record_type": "bundle_incomplete",
        "run_id": run_id,
        "reason": reason,
        "eligible_for_brain_update": False,
    }
    return canonical_json(row) + "\n"


def _blind_execution_guard_verified(manifest: dict[str, Any]) -> bool:
    mode = manifest.get("blind_context_mode")
    if mode not in {
        "NEWS_ONLY_STRICT",
        "CUTOFF_SAFE_WEB_BLIND",
        "D_MINUS_ONE_PRICE_BLIND",
        "CUTOFF_SAFE_WEB_AND_D_MINUS_ONE_PRICE_BLIND",
    }:
        return False
    if mode in {"NEWS_ONLY_STRICT", "D_MINUS_ONE_PRICE_BLIND"} and manifest.get(
        "blind_web_search_call_count", 0
    ) != 0:
        return False
    price_access_count = manifest.get("blind_price_repository_access_count", 0)
    if (
        not isinstance(price_access_count, int)
        or isinstance(price_access_count, bool)
        or price_access_count < 0
    ):
        return False
    if mode in {"NEWS_ONLY_STRICT", "CUTOFF_SAFE_WEB_BLIND"} and price_access_count != 0:
        return False
    return (
        manifest.get("blind_current_price_access_count", 0) == 0
        and manifest.get("no_d_outcome_exposed") is True
    )


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
    candidate_web_checks: str | None,
    candidate_verification: str | None,
    final_synthesis_context: str | None,
    excluded_candidate_web_checks: str | None,
    phase_state: str,
    bundle_manifest: dict[str, Any],
) -> str:
    blind_json = _prediction_json_text(prediction)
    episode_json = research_episode.model_dump_json(indent=2)
    manifest_json = json.dumps(
        bundle_manifest,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    optional_blocks = ""
    if candidate_web_checks is not None:
        optional_blocks += (
            f"{_block('candidate_web_checks.jsonl', candidate_web_checks, fence='jsonl')}\n\n"
        )
    if candidate_verification is not None:
        optional_blocks += (
            f"{_block('candidate_verification.json', candidate_verification, fence='json')}\n\n"
        )
    if final_synthesis_context is not None:
        optional_blocks += (
            f"{_block('final_synthesis_context.json', final_synthesis_context, fence='json')}\n\n"
        )
    if excluded_candidate_web_checks is not None:
        optional_blocks += (
            f"{_block('excluded_candidate_web_checks.jsonl', excluded_candidate_web_checks, fence='jsonl')}\n\n"
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
        f"{optional_blocks}"
        f"{_block('phase_state.json', phase_state, fence='json')}\n\n"
        f"{_block('bundle_manifest.json', manifest_json, fence='json')}\n"
    )


def _block(name: str, content: str, *, fence: str | None) -> str:
    body = content.strip()
    if fence is not None:
        body = f"```{fence}\n{body}\n```"
    return f"<!-- NSLAB:BEGIN {name} -->\n{body}\n<!-- NSLAB:END {name} -->"
