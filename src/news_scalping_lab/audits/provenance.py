"""Output provenance audits."""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.context.episode_scope import inspect_manifest_episode_scope
from news_scalping_lab.context.final_synthesis import final_synthesis_input_summary
from news_scalping_lab.contracts.models import (
    ClaimStatus,
    CompanyMemory,
    ConfidenceLabel,
    FailureCode,
    MechanismMemory,
    PathType,
    RelationClass,
)
from news_scalping_lab.ingest.news import load_news_csv
from news_scalping_lab.reporting.sections import inspect_preopen_report_sections
from news_scalping_lab.research_import.bundle import (
    CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
    EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
    BundleImportError,
    parse_bundle,
)
from news_scalping_lab.research_import.semantic import SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS
from news_scalping_lab.training import KIND_TRAINING_CATEGORIES, REQUIRED_TRAINING_CATEGORIES
from news_scalping_lab.utils import (
    KST,
    canonical_json,
    default_news_window_start,
    file_sha256,
    is_available_as_of,
    next_trading_day,
    parse_datetime,
    read_json,
    sha256_text,
    stable_id,
)

SEMANTIC_IMPORT_SOURCE_TYPE = "semantic_llm_structured_import"
STRICT_IMPORT_SOURCE_TYPE = "strict_research_json"
ALLOWED_CONFIDENCE_LABELS = {label.value for label in ConfidenceLabel}
ALLOWED_CANDIDATE_PATH_TYPES = {path_type.value for path_type in PathType}
ALLOWED_FAILURE_CODES = {code.value for code in FailureCode}
ALLOWED_CLAIM_STATUSES = {status.value for status in ClaimStatus}
ALLOWED_RELATION_CLASSES = {relation_class.value for relation_class in RelationClass}
PREDICTION_STRING_SEQUENCE_FIELDS = (
    "event_ids",
    "causal_chain",
    "direct_evidence",
    "inferred_evidence",
    "market_memory_evidence",
    "prior_positive_cases",
    "prior_negative_cases",
    "counterarguments",
    "disconfirming_conditions",
    "source_urls",
    "memory_episode_ids",
)
POSTMORTEM_STRING_SEQUENCE_FIELDS = (
    "hits",
    "misses",
    "false_positives",
    "lessons",
)
MEMORY_CLAIM_STRING_SEQUENCE_FIELDS = (
    "conditions",
    "failure_modes",
    "support_episode_ids",
    "contradiction_episode_ids",
    "near_miss_episode_ids",
)
RELATION_EVIDENCE_SEQUENCE_FIELDS = (
    "fundamental_evidence",
    "narrative_evidence",
    "market_memory_evidence",
)
OUTCOME_NUMERIC_FIELDS = (
    "open_gap_pct",
    "intraday_high_return_pct",
    "close_return_pct",
    "volume",
    "amount",
    "turnover_ratio",
    "market_cap_previous_close",
)
OUTCOME_BOOLEAN_FIELDS = (
    "upper_limit_touched",
    "upper_limit_closed",
    "upper_limit_released",
    "one_price_upper_limit",
)
OUTCOME_STRING_SEQUENCE_FIELDS = (
    "intraday_fields_unavailable",
    "flags",
)
ELIGIBILITY_BOOLEAN_FIELDS = (
    "forecast_evaluation_eligible",
    "direct_supervised_cases_eligible",
    "theme_supervised_cases_eligible",
    "leader_pair_training_eligible",
    "retrospective_memory_eligible",
    "brain_eligible",
)
BLIND_INTEGRITY_COUNT_FIELDS = (
    "blind_web_search_call_count",
    "blind_price_repository_access_count",
    "blind_current_price_access_count",
)


def audit_provenance(root: Path) -> dict[str, object]:
    findings: list[str] = []
    checked_predictions = 0
    for path in sorted((root / "predictions").glob("*.json")):
        prediction = _read_json_object(path, findings)
        if prediction is None:
            continue
        checked_predictions += 1
        if not prediction.get("blind_artifact_sha256"):
            findings.append(f"{path.name}: missing blind_artifact_sha256")
        _check_blind_artifact_hash(path, prediction, findings)
        context_manifest_id = prediction.get("context_manifest_id")
        manifest = _check_context_manifest(root, path, context_manifest_id, findings)
        _check_report_link(root, path, context_manifest_id, findings)
        if manifest is not None:
            prompt_hashes = _check_manifest_basics(
                root,
                path,
                prediction,
                manifest,
                findings,
            )
            if not isinstance(manifest.get("price_snapshot"), dict):
                findings.append(f"{path.name}: context manifest missing price_snapshot")
            if not isinstance(manifest.get("brain_file_hashes"), dict):
                findings.append(f"{path.name}: context manifest missing brain_file_hashes")
            _check_manifest_context_file_hashes(root, path, manifest, findings)
            _check_manifest_memory_sweep_artifacts(root, path, manifest, findings)
            _check_manifest_output_artifacts(root, path, manifest, findings)
            _check_manifest_model_config(path, manifest, findings)
            _check_manifest_news_input(root, path, manifest, findings)
            _check_prompt_hash_traces(root, path, prompt_hashes, manifest, findings)
            _check_red_team_artifacts(root, path, prediction, manifest, prompt_hashes, findings)
        blind_analysis = prediction.get("blind_analysis", {})
        if not isinstance(blind_analysis, dict) or not blind_analysis.get("provenance"):
            findings.append(f"{path.name}: blind_analysis missing provenance")
        for sector in prediction.get("dominant_sectors", []):
            if not isinstance(sector, dict):
                findings.append(f"{path.name}: dominant sector is not an object")
                continue
            if not sector.get("provenance"):
                findings.append(
                    f"{path.name}: dominant sector missing provenance: {sector.get('name')}"
                )
            has_anchor = (
                sector.get("triggering_events")
                or sector.get("supporting_cases")
                or sector.get("contradicting_cases")
            )
            if not has_anchor:
                findings.append(
                    f"{path.name}: dominant sector lacks provenance anchors: {sector.get('name')}"
                )
        for candidate in prediction.get("candidates", []):
            if not isinstance(candidate, dict):
                findings.append(f"{path.name}: candidate is not an object")
                continue
            if not candidate.get("provenance"):
                findings.append(
                    f"{path.name}: candidate missing provenance: {candidate.get('company_name')}"
                )
            has_anchor = (
                candidate.get("event_ids")
                or candidate.get("memory_episode_ids")
                or candidate.get("source_urls")
            )
            if not has_anchor:
                findings.append(
                    f"{path.name}: candidate lacks provenance anchors: {candidate.get('company_name')}"
                )
    checked_research_episode_files = _check_research_episode_provenance(root, findings)
    checked_evaluation_episode_files = _check_evaluation_episode_provenance(
        root, findings
    )
    checked_company_memory_files = _check_company_memory_provenance(root, findings)
    checked_mechanism_memory_records = _check_mechanism_memory_provenance(root, findings)
    checked_training_export_manifests = _check_training_export_provenance(root, findings)
    checked_analysis_bundles = _check_analysis_bundle_provenance(root, findings)
    return {
        "passed": not findings,
        "findings": findings,
        "checked_predictions": checked_predictions,
        "checked_research_episode_files": checked_research_episode_files,
        "checked_evaluation_episode_files": checked_evaluation_episode_files,
        "checked_company_memory_files": checked_company_memory_files,
        "checked_mechanism_memory_records": checked_mechanism_memory_records,
        "checked_training_export_manifests": checked_training_export_manifests,
        "checked_analysis_bundles": checked_analysis_bundles,
    }


def _check_research_episode_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for path in _iter_research_episode_paths(root):
        episode = _read_json_object(path, findings)
        if episode is None:
            continue
        has_import_provenance = _has_import_provenance(episode)
        is_accepted_episode = path.parent.name == "accepted"
        if not has_import_provenance and not is_accepted_episode:
            continue
        checked += 1
        _check_research_episode_identity(root, path, episode, findings)
        _check_research_episode_metadata(root, path, episode, findings)
        _check_research_episode_top_level_provenance(root, path, episode, findings)
        _check_research_episode_input_news_sources(root, path, episode, findings)
        _check_research_episode_cutoff_at(path, root, episode, findings)
        _check_research_episode_available_from(path, root, episode, findings)
        if is_accepted_episode:
            _check_research_episode_blind_decision_provenance(root, path, episode, findings)
        if _has_semantic_import_provenance(episode):
            _check_semantic_import_audit(root, path, episode, findings)
        if has_import_provenance:
            _check_strict_import_provenance(root, path, episode, findings)
    return checked


def _check_research_episode_identity(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    if episode.get("schema_version") != "nslab.research_episode.v1":
        findings.append(f"{label}: research episode schema_version invalid")
    episode_id = episode.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        findings.append(f"{label}: research episode episode_id missing")
        return
    if episode_path.stem != episode_id:
        findings.append(f"{label}: research episode filename/episode_id mismatch")


def _check_research_episode_metadata(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    if _parse_optional_datetime(episode.get("created_at")) is None:
        findings.append(f"{label}: research episode created_at missing or invalid")
    research_version = episode.get("research_version")
    if not isinstance(research_version, str) or not research_version.strip():
        findings.append(f"{label}: research episode research_version missing or invalid")
    price_source_snapshot = episode.get("price_source_snapshot")
    if not isinstance(price_source_snapshot, dict) or not price_source_snapshot:
        findings.append(f"{label}: research episode price_source_snapshot missing or invalid")


def _check_research_episode_top_level_provenance(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    provenance_entries = _top_level_provenance_entries(episode)
    if not provenance_entries:
        findings.append(f"{label}: research episode missing top-level provenance")
        return
    for index, entry in enumerate(provenance_entries, start=1):
        _check_memory_source(root, label, index, entry, findings, kind="research episode")


def _check_research_episode_input_news_sources(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    input_news_files = episode.get("input_news_files")
    input_news_hashes = episode.get("input_news_hashes")
    if not isinstance(input_news_files, list):
        findings.append(f"{label}: research episode input_news_files missing")
        return
    if not isinstance(input_news_hashes, list):
        findings.append(f"{label}: research episode input_news_hashes missing")
        return
    if len(input_news_files) != len(input_news_hashes):
        findings.append(f"{label}: research episode input_news_files/hash count mismatch")
        return
    for index, (path_ref, expected_hash) in enumerate(
        zip(input_news_files, input_news_hashes, strict=True),
        start=1,
    ):
        if not isinstance(path_ref, str) or not path_ref:
            findings.append(f"{label}: research episode input_news_files {index} invalid")
            continue
        if not isinstance(expected_hash, str) or not expected_hash:
            findings.append(f"{label}: research episode input_news_hashes {index} invalid")
            continue
        source_path = _resolve_project_path(root, path_ref)
        if source_path is None:
            findings.append(f"{label}: research episode input news path escapes project root: {path_ref}")
            continue
        if not source_path.exists():
            findings.append(f"{label}: research episode input news file not found: {path_ref}")
            continue
        if file_sha256(source_path) != expected_hash:
            findings.append(f"{label}: research episode input news hash mismatch: {path_ref}")


def _check_research_episode_cutoff_at(
    episode_path: Path,
    root: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    raw_trade_date = episode.get("trade_date")
    raw_cutoff_at = episode.get("cutoff_at")
    if not isinstance(raw_trade_date, str) or not isinstance(raw_cutoff_at, str):
        findings.append(f"{label}: research episode cutoff_at or trade_date missing")
        return
    try:
        trade_date = date.fromisoformat(raw_trade_date)
        cutoff_at = parse_datetime(raw_cutoff_at)
    except ValueError:
        findings.append(f"{label}: research episode cutoff_at or trade_date invalid")
        return
    latest = datetime.combine(trade_date, time(8, 59, 59), tzinfo=KST)
    if cutoff_at.astimezone(KST) > latest:
        findings.append(f"{label}: research episode cutoff_at is after trade-date cutoff")


def _check_research_episode_available_from(
    episode_path: Path,
    root: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    raw_trade_date = episode.get("trade_date")
    raw_available_from = episode.get("available_from")
    if not isinstance(raw_trade_date, str) or not isinstance(raw_available_from, str):
        findings.append(f"{label}: research episode available_from or trade_date missing")
        return
    try:
        trade_date = date.fromisoformat(raw_trade_date)
        available_from = parse_datetime(raw_available_from)
    except ValueError:
        findings.append(f"{label}: research episode available_from or trade_date invalid")
        return
    earliest = datetime.combine(next_trading_day(trade_date), time(0, 0, 0), tzinfo=KST)
    if available_from.astimezone(KST) < earliest:
        findings.append(f"{label}: research episode available_from precedes next trading day")


def _check_research_episode_blind_decision_provenance(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    episode_available_from = _parse_optional_datetime(episode.get("available_from"))
    blind_analysis = episode.get("blind_analysis")
    if not isinstance(blind_analysis, dict):
        findings.append(f"{label}: research episode blind_analysis missing")
    else:
        _check_research_episode_blind_analysis_shape(label, blind_analysis, findings)
        _check_nested_provenance_entries(
            root,
            label,
            blind_analysis,
            findings,
            kind="research episode blind_analysis",
        )
    blind_predictions = episode.get("blind_predictions")
    if not isinstance(blind_predictions, list):
        findings.append(f"{label}: research episode blind_predictions missing")
        return
    _check_research_episode_blind_prediction_ranks(label, blind_predictions, findings)
    for index, candidate in enumerate(blind_predictions, start=1):
        if not isinstance(candidate, dict):
            findings.append(f"{label}: research episode blind prediction {index} is not an object")
            continue
        _check_research_episode_blind_prediction_shape(label, index, candidate, findings)
        _check_nested_provenance_entries(
            root,
            label,
            candidate,
            findings,
            kind=f"research episode blind prediction {index}",
        )
        has_anchor = (
            candidate.get("event_ids")
            or candidate.get("memory_episode_ids")
            or candidate.get("source_urls")
        )
        if not has_anchor:
            findings.append(
                f"{label}: research episode blind prediction lacks provenance anchors: "
                f"{candidate.get('company_name')}"
            )
    postmortem = episode.get("postmortem")
    if postmortem is not None:
        if not isinstance(postmortem, dict):
            findings.append(f"{label}: research episode postmortem is not an object")
        else:
            _check_research_episode_postmortem_shape(label, postmortem, findings)
            _check_nested_provenance_entries(
                root,
                label,
                postmortem,
                findings,
                kind="research episode postmortem",
            )
    _check_research_episode_execution_metadata_shape(label, episode, findings)
    _check_research_episode_outcome_labels_shape(label, episode.get("outcome_labels"), findings)
    _check_string_list_field(
        f"{label}: research episode",
        "misses",
        episode.get("misses"),
        findings,
    )
    for field_name, kind in (
        ("observed_events", "research episode observed event"),
        ("event_ticker_edges", "research episode event ticker edge"),
        ("lessons", "research episode lesson"),
        ("counterexamples", "research episode counterexample"),
    ):
        _check_research_episode_nested_list_provenance(
            root,
            label,
            episode.get(field_name),
            findings,
            episode_available_from=episode_available_from,
            field_name=field_name,
            kind=kind,
        )


def _check_research_episode_nested_list_provenance(
    root: Path,
    label: str,
    value: Any,
    findings: list[str],
    *,
    episode_available_from: datetime | None,
    field_name: str,
    kind: str,
) -> None:
    if not isinstance(value, list):
        findings.append(f"{label}: research episode {field_name} missing")
        return
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            findings.append(f"{label}: {kind} {index} is not an object")
            continue
        _check_nested_provenance_entries(
            root,
            label,
            item,
            findings,
            kind=f"{kind} {index}",
        )
        if field_name == "observed_events":
            _check_research_episode_observed_event_shape(label, item, findings, index=index)
        if field_name == "event_ticker_edges":
            _check_research_episode_relation_edge_shape(label, item, findings, index=index)
        if field_name in {"lessons", "counterexamples"}:
            _check_research_episode_memory_claim_shape(label, item, findings, kind=kind, index=index)
            _check_research_episode_claim_available_from(
                label,
                item,
                findings,
                episode_available_from=episode_available_from,
                kind=kind,
                index=index,
            )


def _check_research_episode_blind_analysis_shape(
    label: str,
    blind_analysis: dict[str, Any],
    findings: list[str],
) -> None:
    summary = blind_analysis.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        findings.append(f"{label}: research episode blind_analysis summary missing or invalid")
    mechanisms = blind_analysis.get("open_world_mechanisms")
    if not isinstance(mechanisms, list) or not mechanisms or not all(
        isinstance(item, str) and item.strip() for item in mechanisms
    ):
        findings.append(
            f"{label}: research episode blind_analysis open_world_mechanisms missing or invalid"
        )
    initial_uncertainties = blind_analysis.get("initial_uncertainties")
    if not isinstance(initial_uncertainties, list) or not all(
        isinstance(item, str) and item.strip() for item in initial_uncertainties
    ):
        findings.append(
            f"{label}: research episode blind_analysis initial_uncertainties invalid"
        )


def _check_research_episode_observed_event_shape(
    label: str,
    event: dict[str, Any],
    findings: list[str],
    *,
    index: int,
) -> None:
    prefix = f"{label}: research episode observed event {index}"
    for field_name in ("event_id", "title", "body", "source_id"):
        value = event.get(field_name)
        if not isinstance(value, str) or not value.strip():
            findings.append(f"{prefix} {field_name} missing or invalid")
    row_number = event.get("row_number")
    if not isinstance(row_number, int) or row_number < 1:
        findings.append(f"{prefix} row_number missing or invalid")
    if _parse_optional_datetime(event.get("published_at")) is None:
        findings.append(f"{prefix} published_at missing or invalid")
    collected_at = event.get("collected_at")
    if collected_at is not None and _parse_optional_datetime(collected_at) is None:
        findings.append(f"{prefix} collected_at invalid")


def _check_research_episode_relation_edge_shape(
    label: str,
    edge: dict[str, Any],
    findings: list[str],
    *,
    index: int,
) -> None:
    prefix = f"{label}: research episode event ticker edge {index}"
    for field_name in (
        "edge_id",
        "episode_id",
        "event_id",
        "ticker",
        "company_name",
        "relation_explanation",
        "temporal_validity",
    ):
        value = edge.get(field_name)
        if not isinstance(value, str) or not value.strip():
            findings.append(f"{prefix} {field_name} missing or invalid")
    relation_class = edge.get("relation_class")
    if not isinstance(relation_class, str) or relation_class not in ALLOWED_RELATION_CLASSES:
        findings.append(f"{prefix} relation_class missing or invalid")
    if not isinstance(edge.get("directly_mentioned"), bool):
        findings.append(f"{prefix} directly_mentioned missing or invalid")
    confidence_label = edge.get("confidence_label")
    if (
        not isinstance(confidence_label, str)
        or confidence_label not in ALLOWED_CONFIDENCE_LABELS
    ):
        findings.append(f"{prefix} confidence_label missing or invalid")
    for field_name in RELATION_EVIDENCE_SEQUENCE_FIELDS:
        _check_string_list_field(prefix, field_name, edge.get(field_name), findings)


def _check_research_episode_outcome_labels_shape(
    label: str,
    outcome_labels: Any,
    findings: list[str],
) -> None:
    if not isinstance(outcome_labels, dict):
        findings.append(f"{label}: research episode outcome_labels missing or invalid")
        return
    for outcome_key, outcome in outcome_labels.items():
        if not isinstance(outcome_key, str) or not outcome_key.strip():
            findings.append(f"{label}: research episode outcome label key missing or invalid")
            continue
        if not isinstance(outcome, dict):
            findings.append(
                f"{label}: research episode outcome label {outcome_key} is not an object"
            )
            continue
        prefix = f"{label}: research episode outcome label {outcome_key}"
        for field_name in OUTCOME_NUMERIC_FIELDS:
            value = outcome.get(field_name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int | float)
            ):
                findings.append(f"{prefix} {field_name} invalid")
        for field_name in OUTCOME_BOOLEAN_FIELDS:
            value = outcome.get(field_name)
            if value is not None and not isinstance(value, bool):
                findings.append(f"{prefix} {field_name} invalid")
        for field_name in OUTCOME_STRING_SEQUENCE_FIELDS:
            _check_string_list_field(prefix, field_name, outcome.get(field_name), findings)


def _check_research_episode_execution_metadata_shape(
    label: str,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    execution_protocol_version = episode.get("execution_protocol_version")
    if execution_protocol_version is not None and (
        not isinstance(execution_protocol_version, str)
        or not execution_protocol_version.strip()
    ):
        findings.append(
            f"{label}: research episode execution_protocol_version invalid"
        )

    outcome_coverage_status = episode.get("outcome_coverage_status")
    if outcome_coverage_status is not None and (
        not isinstance(outcome_coverage_status, str)
        or not outcome_coverage_status.strip()
    ):
        findings.append(f"{label}: research episode outcome_coverage_status invalid")

    eligibility_matrix = episode.get("eligibility_matrix")
    if eligibility_matrix is not None:
        _check_research_episode_eligibility_matrix_shape(
            label,
            eligibility_matrix,
            findings,
        )

    blind_integrity = episode.get("blind_integrity")
    if blind_integrity is not None:
        _check_research_episode_blind_integrity_shape(label, blind_integrity, findings)

    blind_seal_receipt = episode.get("blind_seal_receipt")
    if blind_seal_receipt is not None:
        _check_research_episode_blind_seal_receipt_shape(
            label,
            blind_seal_receipt,
            findings,
        )


def _check_research_episode_eligibility_matrix_shape(
    label: str,
    eligibility_matrix: Any,
    findings: list[str],
) -> None:
    if not isinstance(eligibility_matrix, dict):
        findings.append(f"{label}: research episode eligibility_matrix invalid")
        return
    for field_name in ELIGIBILITY_BOOLEAN_FIELDS:
        if not isinstance(eligibility_matrix.get(field_name), bool):
            findings.append(
                f"{label}: research episode eligibility_matrix {field_name} invalid"
            )
    reasons = eligibility_matrix.get("reasons")
    if not isinstance(reasons, dict) or not all(
        isinstance(key, str)
        and key
        and isinstance(value, str)
        and value
        for key, value in reasons.items()
    ):
        findings.append(f"{label}: research episode eligibility_matrix reasons invalid")


def _check_research_episode_blind_integrity_shape(
    label: str,
    blind_integrity: Any,
    findings: list[str],
) -> None:
    if not isinstance(blind_integrity, dict):
        findings.append(f"{label}: research episode blind_integrity invalid")
        return
    if not blind_integrity:
        return
    mode = blind_integrity.get("blind_context_mode")
    if not isinstance(mode, str) or not mode.strip():
        findings.append(
            f"{label}: research episode blind_integrity blind_context_mode invalid"
        )
    for field_name in BLIND_INTEGRITY_COUNT_FIELDS:
        value = blind_integrity.get(field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            findings.append(
                f"{label}: research episode blind_integrity {field_name} invalid"
            )
    if blind_integrity.get("blind_current_price_access_count") != 0:
        findings.append(
            f"{label}: research episode blind_integrity blind_current_price_access_count "
            "must be zero"
        )
    if blind_integrity.get("no_d_outcome_exposed") is not True:
        findings.append(
            f"{label}: research episode blind_integrity no_d_outcome_exposed "
            "must be true"
        )


def _check_research_episode_blind_seal_receipt_shape(
    label: str,
    blind_seal_receipt: Any,
    findings: list[str],
) -> None:
    if not isinstance(blind_seal_receipt, dict):
        findings.append(f"{label}: research episode blind_seal_receipt invalid")
        return
    if not blind_seal_receipt:
        return
    if blind_seal_receipt.get("schema_version") != "nslab.blind_seal_receipt.v1":
        findings.append(
            f"{label}: research episode blind_seal_receipt schema_version invalid"
        )
    if blind_seal_receipt.get("phase") != "BLIND_SEALED":
        findings.append(f"{label}: research episode blind_seal_receipt phase invalid")
    blind_hash = blind_seal_receipt.get("blind_artifact_sha256")
    if not isinstance(blind_hash, str) or not blind_hash.strip():
        findings.append(
            f"{label}: research episode blind_seal_receipt blind_artifact_sha256 invalid"
        )
    if blind_seal_receipt.get("no_d_outcome_exposed") is not True:
        findings.append(
            f"{label}: research episode blind_seal_receipt no_d_outcome_exposed "
            "must be true"
        )


def _check_research_episode_blind_prediction_shape(
    label: str,
    index: int,
    candidate: dict[str, Any],
    findings: list[str],
) -> None:
    prefix = f"{label}: research episode blind prediction {index}"
    rank = candidate.get("rank")
    if not isinstance(rank, int) or rank < 1:
        findings.append(f"{prefix} rank missing or invalid")
    for field_name in ("ticker", "company_name", "thesis", "why_now"):
        value = candidate.get(field_name)
        if not isinstance(value, str) or not value.strip():
            findings.append(f"{prefix} {field_name} missing or invalid")
    path_type = candidate.get("path_type")
    if not isinstance(path_type, str) or path_type not in ALLOWED_CANDIDATE_PATH_TYPES:
        findings.append(f"{prefix} path_type missing or invalid")
    confidence_label = candidate.get("confidence_label")
    if (
        not isinstance(confidence_label, str)
        or confidence_label not in ALLOWED_CONFIDENCE_LABELS
    ):
        findings.append(f"{prefix} confidence_label missing or invalid")
    evidence_quality = candidate.get("evidence_quality")
    if not isinstance(evidence_quality, str) or evidence_quality not in ALLOWED_CONFIDENCE_LABELS:
        findings.append(f"{prefix} evidence_quality missing or invalid")
    for field_name in PREDICTION_STRING_SEQUENCE_FIELDS:
        _check_string_list_field(prefix, field_name, candidate.get(field_name), findings)


def _check_research_episode_blind_prediction_ranks(
    label: str,
    blind_predictions: list[Any],
    findings: list[str],
) -> None:
    ranks = [
        candidate.get("rank")
        for candidate in blind_predictions
        if isinstance(candidate, dict)
    ]
    if ranks != list(range(1, len(ranks) + 1)):
        findings.append(f"{label}: research episode blind prediction ranks are not sequential")


def _check_string_list_field(
    label: str,
    field_name: str,
    value: Any,
    findings: list[str],
) -> None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        findings.append(f"{label} {field_name} missing or invalid")


def _check_research_episode_postmortem_shape(
    label: str,
    postmortem: dict[str, Any],
    findings: list[str],
) -> None:
    summary = postmortem.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        findings.append(f"{label}: research episode postmortem summary missing or invalid")
    for field_name in POSTMORTEM_STRING_SEQUENCE_FIELDS:
        _check_string_list_field(
            f"{label}: research episode postmortem",
            field_name,
            postmortem.get(field_name),
            findings,
        )
    failure_codes = postmortem.get("failure_codes")
    if not isinstance(failure_codes, list) or not all(
        isinstance(code, str) and code in ALLOWED_FAILURE_CODES for code in failure_codes
    ):
        findings.append(f"{label}: research episode postmortem failure_codes invalid")


def _check_research_episode_claim_available_from(
    label: str,
    claim: dict[str, Any],
    findings: list[str],
    *,
    episode_available_from: datetime | None,
    kind: str,
    index: int,
) -> None:
    raw_available_from = claim.get("available_from")
    if not isinstance(raw_available_from, str):
        findings.append(f"{label}: {kind} {index} available_from missing")
        return
    claim_available_from = _parse_optional_datetime(raw_available_from)
    if claim_available_from is None:
        findings.append(f"{label}: {kind} {index} available_from invalid")
        return
    if episode_available_from is not None and not is_available_as_of(
        episode_available_from,
        claim_available_from,
    ):
        findings.append(f"{label}: {kind} {index} available_from precedes episode")


def _check_research_episode_memory_claim_shape(
    label: str,
    claim: dict[str, Any],
    findings: list[str],
    *,
    kind: str,
    index: int,
) -> None:
    prefix = f"{label}: {kind} {index}"
    for field_name in ("claim_id", "statement", "mechanism", "scope"):
        value = claim.get(field_name)
        if not isinstance(value, str) or not value.strip():
            findings.append(f"{prefix} {field_name} missing or invalid")
    for field_name in MEMORY_CLAIM_STRING_SEQUENCE_FIELDS:
        _check_string_list_field(prefix, field_name, claim.get(field_name), findings)
    status = claim.get("status")
    if not isinstance(status, str) or status not in ALLOWED_CLAIM_STATUSES:
        findings.append(f"{prefix} status missing or invalid")
    confidence_label = claim.get("confidence_label")
    if (
        not isinstance(confidence_label, str)
        or confidence_label not in ALLOWED_CONFIDENCE_LABELS
    ):
        findings.append(f"{prefix} confidence_label missing or invalid")
    first_observed_at = claim.get("first_observed_at")
    if first_observed_at is not None:
        try:
            if not isinstance(first_observed_at, str):
                raise ValueError
            date.fromisoformat(first_observed_at)
        except ValueError:
            findings.append(f"{prefix} first_observed_at invalid")
    last_updated_at = claim.get("last_updated_at")
    if last_updated_at is not None and _parse_optional_datetime(last_updated_at) is None:
        findings.append(f"{prefix} last_updated_at invalid")


def _parse_optional_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _check_nested_provenance_entries(
    root: Path,
    label: str,
    payload: dict[str, Any],
    findings: list[str],
    *,
    kind: str,
) -> None:
    provenance_entries = payload.get("provenance")
    if not isinstance(provenance_entries, list) or not provenance_entries:
        findings.append(f"{label}: {kind} missing provenance")
        return
    for index, entry in enumerate(provenance_entries, start=1):
        if not isinstance(entry, dict):
            findings.append(f"{label}: {kind} provenance {index} is invalid")
            continue
        _check_memory_source(root, label, index, entry, findings, kind=kind)


def _check_evaluation_episode_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for path in _iter_research_episode_paths(root):
        episode = _read_json_object(path, findings)
        if episode is None or not _has_current_evaluation_postmortem_provenance(
            episode
        ):
            continue
        checked += 1
        label = _display_path(root, path)
        _check_evaluation_episode_sources(root, label, episode, findings)
        _check_evaluation_episode_available_from(label, episode, findings)
    return checked


def _check_company_memory_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for path in sorted((root / "memory" / "company_memory").glob("*.json")):
        memory = _read_json_object(path, findings)
        if memory is None:
            continue
        checked += 1
        label = _display_path(root, path)
        try:
            CompanyMemory.model_validate(memory)
        except ValidationError as exc:
            findings.append(f"{label}: company memory schema invalid: {exc}")
            continue
        provenance_entries = memory.get("provenance")
        if not isinstance(provenance_entries, list) or not provenance_entries:
            findings.append(f"{label}: company memory missing provenance")
            continue
        for index, entry in enumerate(provenance_entries, start=1):
            if not isinstance(entry, dict):
                findings.append(f"{label}: company memory provenance {index} is invalid")
                continue
            _check_memory_source(root, label, index, entry, findings, kind="company memory")
    return checked


def _check_mechanism_memory_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    path = root / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
    if not path.exists():
        return checked
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        checked += 1
        label = f"{_display_path(root, path)}:{line_number}"
        try:
            raw = json.loads(line)
            memory = MechanismMemory.model_validate(raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            findings.append(f"{label}: mechanism memory schema invalid: {exc}")
            continue
        if not memory.provenance:
            findings.append(f"{label}: mechanism memory missing provenance")
            continue
        for index, entry in enumerate(memory.provenance, start=1):
            _check_memory_source(
                root,
                label,
                index,
                entry.model_dump(mode="json"),
                findings,
                kind="mechanism memory",
            )
    return checked


def _check_memory_source(
    root: Path,
    label: str,
    index: int,
    entry: dict[str, Any],
    findings: list[str],
    *,
    kind: str,
) -> None:
    for field in ("source_id", "source_type", "uri"):
        if not isinstance(entry.get(field), str) or not entry.get(field):
            findings.append(f"{label}: {kind} provenance {index} missing {field}")
    uri = entry.get("uri")
    if not isinstance(uri, str) or not uri or _is_external_uri(uri):
        return
    source_path = _resolve_project_path(root, uri)
    if source_path is None:
        findings.append(f"{label}: {kind} provenance {index} uri escapes project root")
        return
    if not source_path.exists():
        findings.append(f"{label}: {kind} provenance {index} source file not found: {uri}")
        return
    expected_hash = entry.get("content_sha256")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(f"{label}: {kind} provenance {index} missing content_sha256")
        return
    if file_sha256(source_path) != expected_hash:
        findings.append(f"{label}: {kind} provenance {index} content_sha256 mismatch")


def _read_local_json_source(
    root: Path,
    entry: dict[str, Any],
    findings: list[str],
) -> dict[str, Any] | None:
    uri = entry.get("uri")
    if not isinstance(uri, str) or not uri or _is_external_uri(uri):
        return None
    source_path = _resolve_project_path(root, uri)
    if source_path is None or not source_path.exists():
        return None
    return _read_json_object(source_path, findings)


def _without_provenance(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _without_provenance(item)
            for key, item in value.items()
            if key != "provenance"
        }
    if isinstance(value, list):
        return [_without_provenance(item) for item in value]
    return value


def _check_blind_artifact_hash(
    prediction_path: Path,
    prediction: dict[str, Any],
    findings: list[str],
) -> None:
    blind_artifact_sha256 = prediction.get("blind_artifact_sha256")
    sealed_at = prediction.get("sealed_at")
    if not isinstance(blind_artifact_sha256, str) or not isinstance(sealed_at, str):
        return
    payload_for_hash = {**prediction, "blind_artifact_sha256": None}
    expected_hash = sha256_text(canonical_json(payload_for_hash))
    if blind_artifact_sha256 != expected_hash:
        findings.append(f"{prediction_path.name}: blind_artifact_sha256 mismatch")


def _iter_research_episode_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory in (
        root / "research" / "episodes",
        root / "research" / "accepted",
        root / "research" / "rejected",
    ):
        paths.extend(sorted(directory.glob("*.json")))
    return paths


def _has_semantic_import_provenance(episode: dict[str, Any]) -> bool:
    input_audit = episode.get("input_audit")
    if isinstance(input_audit, dict) and "semantic_import" in input_audit:
        return True
    return any(
        entry.get("source_type") == SEMANTIC_IMPORT_SOURCE_TYPE
        for entry in _iter_provenance_entries(episode)
    )


def _has_import_provenance(episode: dict[str, Any]) -> bool:
    return _has_semantic_import_provenance(episode) or any(
        entry.get("source_type") == STRICT_IMPORT_SOURCE_TYPE
        for entry in _iter_provenance_entries(episode)
    )


def _has_current_evaluation_postmortem_provenance(episode: dict[str, Any]) -> bool:
    entries = _top_level_provenance_entries(episode)
    return any(
        entry.get("source_type") == "evaluation_postmortem"
        and _is_evaluation_checkpoint_uri(entry.get("uri"))
        for entry in entries
    ) and any(
        entry.get("source_type") == "sealed_blind_prediction"
        and _is_evaluation_checkpoint_uri(entry.get("uri"))
        for entry in entries
    )


def _is_evaluation_checkpoint_uri(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.replace("\\", "/")
    return "/runs/checkpoints/evaluations/" in normalized or normalized.startswith(
        "runs/checkpoints/evaluations/"
    )


def _check_evaluation_episode_sources(
    root: Path,
    label: str,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    provenance_entries = _top_level_provenance_entries(episode)
    sealed_prediction: dict[str, Any] | None = None
    sealed_prediction_sha256: str | None = None
    evaluation_report: dict[str, Any] | None = None
    source_types = {
        entry.get("source_type")
        for entry in provenance_entries
        if isinstance(entry.get("source_type"), str)
    }
    for required_type in ("sealed_blind_prediction", "evaluation_postmortem"):
        if required_type not in source_types:
            findings.append(f"{label}: {required_type} provenance entry missing")
    postmortem = episode.get("postmortem")
    if not isinstance(postmortem, dict):
        findings.append(f"{label}: evaluation postmortem payload missing")
    if not isinstance(episode.get("eligibility_matrix"), dict):
        findings.append(f"{label}: evaluation eligibility_matrix missing")
    if not isinstance(episode.get("outcome_coverage_status"), str):
        findings.append(f"{label}: evaluation outcome_coverage_status missing")
    for index, entry in enumerate(provenance_entries, start=1):
        source_type = entry.get("source_type")
        if source_type == "evaluation_postmortem":
            _check_memory_source(root, label, index, entry, findings, kind="evaluation postmortem")
            evaluation_report = _read_local_json_source(root, entry, findings)
        elif source_type == "sealed_blind_prediction":
            _check_memory_source(root, label, index, entry, findings, kind="sealed blind prediction")
            sealed_prediction = _read_local_json_source(root, entry, findings)
            if isinstance(entry.get("content_sha256"), str):
                sealed_prediction_sha256 = entry["content_sha256"]
    if sealed_prediction is not None:
        _check_evaluation_sealed_prediction_payload(
            label,
            episode,
            sealed_prediction,
            findings,
        )
    if evaluation_report is not None:
        _check_evaluation_report_payload(
            label,
            evaluation_report,
            episode,
            findings,
            sealed_prediction=sealed_prediction,
            sealed_prediction_sha256=sealed_prediction_sha256,
        )


def _check_evaluation_report_payload(
    label: str,
    report: dict[str, Any],
    episode: dict[str, Any],
    findings: list[str],
    *,
    sealed_prediction: dict[str, Any] | None,
    sealed_prediction_sha256: str | None,
) -> None:
    if report.get("schema_version") != "nslab.evaluation.v1":
        findings.append(f"{label}: evaluation report schema_version invalid")
    if report.get("execution_protocol_version") != episode.get("execution_protocol_version"):
        findings.append(f"{label}: evaluation report execution_protocol_version mismatch")
    if report.get("trade_date") != episode.get("trade_date"):
        findings.append(f"{label}: evaluation report trade_date mismatch")
    if sealed_prediction is not None and report.get("blind_prediction_id") != sealed_prediction.get(
        "prediction_id"
    ):
        findings.append(f"{label}: evaluation report blind_prediction_id mismatch")
    if sealed_prediction_sha256 is not None and report.get("blind_prediction_sha256") != (
        sealed_prediction_sha256
    ):
        findings.append(f"{label}: evaluation report blind_prediction_sha256 mismatch")
    if _postmortem_content(report.get("postmortem")) != _postmortem_content(
        episode.get("postmortem")
    ):
        findings.append(f"{label}: evaluation report postmortem mismatch")
    if report.get("eligibility_matrix") != episode.get("eligibility_matrix"):
        findings.append(f"{label}: evaluation report eligibility_matrix mismatch")
    if report.get("outcome_coverage_status") != episode.get("outcome_coverage_status"):
        findings.append(f"{label}: evaluation report outcome_coverage_status mismatch")
    _check_evaluation_report_outcomes(label, report, episode, findings)
    _check_evaluation_report_metrics(label, report, episode, findings)


def _check_evaluation_sealed_prediction_payload(
    label: str,
    episode: dict[str, Any],
    prediction: dict[str, Any],
    findings: list[str],
) -> None:
    if prediction.get("schema_version") != "nslab.blind_prediction.v1":
        findings.append(f"{label}: sealed blind prediction schema_version invalid")
    if not isinstance(prediction.get("sealed_at"), str):
        findings.append(f"{label}: sealed blind prediction sealed_at missing")
    if not isinstance(prediction.get("blind_artifact_sha256"), str):
        findings.append(f"{label}: sealed blind prediction blind_artifact_sha256 missing")
    if prediction.get("trade_date") != episode.get("trade_date"):
        findings.append(f"{label}: sealed blind prediction trade_date mismatch")
    if prediction.get("cutoff_at") != episode.get("cutoff_at"):
        findings.append(f"{label}: sealed blind prediction cutoff_at mismatch")
    if _without_provenance(prediction.get("blind_analysis")) != _without_provenance(
        episode.get("blind_analysis")
    ):
        findings.append(f"{label}: sealed blind prediction blind_analysis mismatch")
    if _without_provenance(prediction.get("candidates")) != _without_provenance(
        episode.get("blind_predictions")
    ):
        findings.append(f"{label}: sealed blind prediction blind_predictions mismatch")


def _check_evaluation_report_outcomes(
    label: str,
    report: dict[str, Any],
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    report_outcomes = report.get("outcomes")
    episode_outcomes = episode.get("outcome_labels")
    blind_predictions = episode.get("blind_predictions")
    if not isinstance(report_outcomes, dict) or not isinstance(episode_outcomes, dict):
        return
    if not isinstance(blind_predictions, list):
        return
    for candidate in blind_predictions:
        if not isinstance(candidate, dict):
            continue
        rank = candidate.get("rank")
        ticker = candidate.get("ticker")
        company = candidate.get("company_name")
        if not isinstance(rank, int) or not isinstance(ticker, str) or not isinstance(company, str):
            continue
        outcome_key = f"{rank}:{ticker}:{company}"
        if episode_outcomes.get(outcome_key) != report_outcomes.get(company):
            findings.append(
                f"{label}: evaluation report outcome mismatch for {outcome_key}"
            )
    expected_count = len(
        [
            candidate
            for candidate in blind_predictions
            if isinstance(candidate, dict) and isinstance(candidate.get("company_name"), str)
        ]
    )
    if len(report_outcomes) != expected_count:
        findings.append(f"{label}: evaluation report outcome count mismatch")


def _check_evaluation_report_metrics(
    label: str,
    report: dict[str, Any],
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    metrics = report.get("performance_metrics")
    blind_predictions = episode.get("blind_predictions")
    if not isinstance(metrics, dict) or not isinstance(blind_predictions, list):
        return
    candidate_count = metrics.get("candidate_count")
    if candidate_count != len(blind_predictions):
        findings.append(f"{label}: evaluation report candidate_count mismatch")


def _top_level_provenance_entries(episode: dict[str, Any]) -> list[dict[str, Any]]:
    entries = episode.get("provenance")
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _postmortem_content(value: object) -> object:
    if not isinstance(value, dict):
        return value
    return {key: item for key, item in value.items() if key != "provenance"}


def _check_evaluation_episode_available_from(
    label: str,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    raw_trade_date = episode.get("trade_date")
    raw_available_from = episode.get("available_from")
    if not isinstance(raw_trade_date, str) or not isinstance(raw_available_from, str):
        findings.append(f"{label}: evaluation available_from or trade_date missing")
        return
    try:
        trade_date = date.fromisoformat(raw_trade_date)
        available_from = parse_datetime(raw_available_from)
    except ValueError:
        findings.append(f"{label}: evaluation available_from or trade_date invalid")
        return
    expected = datetime.combine(next_trading_day(trade_date), time(0, 0, 0), tzinfo=KST)
    if available_from.astimezone(KST) != expected:
        findings.append(f"{label}: evaluation available_from is not next trading day")


def _check_strict_import_provenance(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    for index, entry in enumerate(_iter_provenance_entries(episode), start=1):
        if entry.get("source_type") != STRICT_IMPORT_SOURCE_TYPE:
            continue
        _check_memory_source(root, label, index, entry, findings, kind="strict import")


def _check_semantic_import_audit(
    root: Path,
    episode_path: Path,
    episode: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, episode_path)
    input_audit = episode.get("input_audit")
    if not isinstance(input_audit, dict):
        findings.append(f"{label}: semantic_import input_audit missing")
        return
    semantic = input_audit.get("semantic_import")
    if not isinstance(semantic, dict):
        findings.append(f"{label}: semantic_import audit missing")
        return

    provenance_entries = [
        entry
        for entry in _iter_provenance_entries(episode)
        if entry.get("source_type") == SEMANTIC_IMPORT_SOURCE_TYPE
    ]
    if not provenance_entries:
        findings.append(f"{label}: semantic_import provenance entry missing")

    source_path = _resolve_semantic_source_path(root, label, semantic, findings)
    source_text: str | None = None
    source_hash: str | None = None
    if source_path is not None and source_path.exists():
        source_hash = file_sha256(source_path)
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        if semantic.get("source_sha256") != source_hash:
            findings.append(f"{label}: semantic_import source_sha256 mismatch")
        if semantic.get("source_text_sha256") != sha256_text(source_text):
            findings.append(f"{label}: semantic_import source_text_sha256 mismatch")

    _check_semantic_provenance_entries(root, label, semantic, source_hash, provenance_entries, findings)
    _check_semantic_source_segments(label, semantic, source_text, findings)
    _check_semantic_output_sources(label, episode, semantic, findings)


def _resolve_semantic_source_path(
    root: Path,
    label: str,
    semantic: dict[str, Any],
    findings: list[str],
) -> Path | None:
    source_ref = semantic.get("source_path")
    if not isinstance(source_ref, str) or not source_ref:
        findings.append(f"{label}: semantic_import source_path missing")
        return None
    source_path = _resolve_project_path(root, source_ref)
    if source_path is None:
        findings.append(f"{label}: semantic_import source_path escapes project root")
        return None
    if not source_path.exists():
        findings.append(f"{label}: semantic_import source file not found: {source_ref}")
        return None
    return source_path


def _check_semantic_provenance_entries(
    root: Path,
    label: str,
    semantic: dict[str, Any],
    source_hash: str | None,
    provenance_entries: list[dict[str, Any]],
    findings: list[str],
) -> None:
    source_ref = semantic.get("source_path")
    for entry in provenance_entries:
        if source_hash is not None and entry.get("content_sha256") != source_hash:
            findings.append(f"{label}: semantic_import provenance content_sha256 mismatch")
        uri = entry.get("uri")
        if (
            isinstance(source_ref, str)
            and isinstance(uri, str)
            and not _same_project_path(root, source_ref, uri)
        ):
            findings.append(f"{label}: semantic_import provenance uri mismatch")


def _check_semantic_source_segments(
    label: str,
    semantic: dict[str, Any],
    source_text: str | None,
    findings: list[str],
) -> None:
    source_segments = semantic.get("source_segments")
    if not isinstance(source_segments, list) or not source_segments:
        findings.append(f"{label}: semantic_import source_segments missing")
        return
    if semantic.get("source_segment_count") != len(source_segments):
        findings.append(f"{label}: semantic_import source_segment_count mismatch")
    if semantic.get("source_segments_sha256") != sha256_text(canonical_json(source_segments)):
        findings.append(f"{label}: semantic_import source_segments_sha256 mismatch")
    if source_text is None:
        return

    previous_end = -1
    for expected_index, segment in enumerate(source_segments, start=1):
        if not isinstance(segment, dict):
            findings.append(f"{label}: semantic_import segment {expected_index} is invalid")
            continue
        start = segment.get("char_start")
        end = segment.get("char_end")
        index = segment.get("index")
        if index != expected_index:
            findings.append(f"{label}: semantic_import segment {expected_index} index mismatch")
        if (
            not isinstance(start, int)
            or not isinstance(end, int)
            or start < 0
            or end <= start
            or end > len(source_text)
        ):
            findings.append(f"{label}: semantic_import segment {expected_index} span invalid")
            continue
        if start < previous_end:
            findings.append(f"{label}: semantic_import segment {expected_index} overlaps previous")
        previous_end = end
        segment_text = source_text[start:end]
        if segment_text != segment_text.strip():
            findings.append(f"{label}: semantic_import segment {expected_index} span not trimmed")
        if segment.get("text_sha256") != sha256_text(segment_text):
            findings.append(f"{label}: semantic_import segment {expected_index} text_sha256 mismatch")
        if segment.get("excerpt") != segment_text[:240]:
            findings.append(f"{label}: semantic_import segment {expected_index} excerpt mismatch")


def _check_semantic_output_sources(
    label: str,
    episode: dict[str, Any],
    semantic: dict[str, Any],
    findings: list[str],
) -> None:
    output_field_source_ids = semantic.get("output_field_source_ids")
    if not isinstance(output_field_source_ids, dict) or not output_field_source_ids:
        findings.append(f"{label}: semantic_import output_field_source_ids missing")
        return
    known_source_ids = {
        entry.get("source_id")
        for entry in _iter_provenance_entries(episode)
        if isinstance(entry.get("source_id"), str)
    }
    missing_required_fields = [
        field_name
        for field_name in SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS
        if field_name not in output_field_source_ids
    ]
    if missing_required_fields:
        findings.append(
            f"{label}: semantic_import output_field_source_ids missing required fields: "
            f"{', '.join(missing_required_fields)}"
        )
    for field_name, source_ids in output_field_source_ids.items():
        if not isinstance(field_name, str) or not field_name:
            findings.append(f"{label}: semantic_import output field name invalid")
            continue
        if not isinstance(source_ids, list) or not source_ids:
            findings.append(f"{label}: semantic_import output field source ids invalid: {field_name}")
            continue
        for source_id in source_ids:
            if not isinstance(source_id, str) or source_id not in known_source_ids:
                findings.append(
                    f"{label}: semantic_import output field source id unknown: {field_name}"
                )


def _iter_provenance_entries(value: Any) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    if isinstance(value, dict):
        provenance = value.get("provenance")
        if isinstance(provenance, list):
            entries.extend(entry for entry in provenance if isinstance(entry, dict))
        for child in value.values():
            if isinstance(child, dict | list):
                entries.extend(_iter_provenance_entries(child))
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict | list):
                entries.extend(_iter_provenance_entries(item))
    return entries


def _resolve_project_path(root: Path, path_ref: str) -> Path | None:
    resolved_root = root.resolve()
    path = Path(path_ref)
    resolved_path = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_path


def _same_project_path(root: Path, left: str, right: str) -> bool:
    left_path = _resolve_project_path(root, left)
    right_path = _resolve_project_path(root, right)
    if left_path is None or right_path is None:
        return left == right
    return left_path == right_path


def _is_external_uri(uri: str) -> bool:
    return "://" in uri


def _display_path(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.name


def _check_manifest_basics(
    root: Path,
    prediction_path: Path,
    prediction: dict[str, Any],
    manifest: dict[str, Any],
    findings: list[str],
) -> dict[str, Any]:
    _check_manifest_reproducibility_fields(root, prediction_path, manifest, findings)
    prompt_hashes = manifest.get("prompt_hashes", {})
    if not isinstance(prompt_hashes, dict):
        findings.append(f"{prediction_path.name}: context manifest prompt_hashes is not an object")
        prompt_hashes = {}
    _check_current_manifest_required_contract(
        prediction_path,
        prediction,
        manifest,
        prompt_hashes,
        findings,
    )
    if not prompt_hashes.get("blind_analysis"):
        findings.append(f"{prediction_path.name}: context manifest missing blind_analysis prompt hash")

    for field in ("trade_date", "cutoff_at"):
        prediction_value = prediction.get(field)
        manifest_value = manifest.get(field)
        if prediction_value is not None and prediction_value != manifest_value:
            findings.append(f"{prediction_path.name}: context manifest {field} mismatch")
    _check_manifest_blind_hash(prediction_path, prediction, manifest, findings)

    token_counts = manifest.get("token_counts", {})
    final_synthesis_was_run = (
        "final_synthesis" in prompt_hashes
        or isinstance(token_counts, dict)
        and "final_synthesis_prompt" in token_counts
    )
    if final_synthesis_was_run and not prompt_hashes.get("final_synthesis"):
        findings.append(f"{prediction_path.name}: context manifest missing final_synthesis prompt hash")
    return prompt_hashes


def _check_current_manifest_required_contract(
    prediction_path: Path,
    prediction: dict[str, Any],
    manifest: dict[str, Any],
    prompt_hashes: dict[str, Any],
    findings: list[str],
) -> None:
    if manifest.get("schema_version") != "nslab.context_manifest.v1":
        return
    if not isinstance(prediction.get("sealed_at"), str) or not prediction.get("sealed_at"):
        return

    required_string_fields = (
        "news_file",
        "news_sha256",
        "prediction_artifact",
        "prediction_sha256",
        "report_artifact",
        "report_sha256",
        "row_disposition_artifact",
        "row_disposition_sha256",
        "event_cluster_artifact",
        "event_cluster_sha256",
        "news_novelty_review_artifact",
        "news_novelty_review_sha256",
        "semantic_retrieval_plan_artifact",
        "semantic_retrieval_plan_sha256",
        "semantic_retrieval_artifact",
        "semantic_retrieval_sha256",
        "candidate_expansion_artifact",
        "candidate_expansion_sha256",
        "source_ledger_artifact",
        "source_ledger_sha256",
        "blind_seal_receipt_artifact",
        "blind_seal_receipt_sha256",
        "phase_state_artifact",
        "phase_state_sha256",
        "final_synthesis_context_artifact",
        "final_synthesis_context_sha256",
    )
    for field in required_string_fields:
        value = manifest.get(field)
        if not isinstance(value, str) or not value:
            findings.append(f"{prediction_path.name}: context manifest missing {field}")

    red_team_artifacts = manifest.get("red_team_artifacts")
    if (
        not isinstance(red_team_artifacts, list)
        or not red_team_artifacts
        or not all(isinstance(item, str) and item for item in red_team_artifacts)
    ):
        findings.append(f"{prediction_path.name}: context manifest red_team_artifacts is invalid")

    for purpose in (
        "news_novelty_review",
        "semantic_retrieval_plan",
        "candidate_expansion",
        "blind_analysis",
        "red_team_candidate_review",
        "final_synthesis",
    ):
        if not isinstance(prompt_hashes.get(purpose), str) or not prompt_hashes.get(purpose):
            findings.append(f"{prediction_path.name}: context manifest missing {purpose} prompt hash")


def _check_manifest_reproducibility_fields(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    if manifest.get("schema_version") != "nslab.context_manifest.v1":
        return
    if "model_config" not in manifest:
        findings.append(f"{prediction_path.name}: context manifest missing model_config")
    _check_manifest_token_counts(prediction_path, manifest, findings)
    for field in ("truncations", "web_queries", "web_sources"):
        value = manifest.get(field)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            findings.append(f"{prediction_path.name}: context manifest {field} is invalid")
    episode_scope = inspect_manifest_episode_scope(root, manifest)
    for error in episode_scope.get("errors", []):
        if isinstance(error, str):
            findings.append(f"{prediction_path.name}: context manifest episode scope {error}")


def _check_manifest_token_counts(
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    value = manifest.get("token_counts")
    if not isinstance(value, dict) or not value:
        findings.append(f"{prediction_path.name}: context manifest token_counts is invalid")
        return
    for key, count in value.items():
        if not isinstance(key, str) or not key:
            findings.append(f"{prediction_path.name}: context manifest token_counts is invalid")
            return
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            findings.append(f"{prediction_path.name}: context manifest token_counts is invalid")
            return


def _check_manifest_blind_hash(
    prediction_path: Path,
    prediction: dict[str, Any],
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    prediction_hash = prediction.get("blind_artifact_sha256")
    manifest_hash = manifest.get("blind_artifact_sha256")
    if prediction.get("sealed_at") and not isinstance(manifest_hash, str):
        findings.append(f"{prediction_path.name}: context manifest missing blind_artifact_sha256")
        return
    if (
        isinstance(prediction_hash, str)
        and isinstance(manifest_hash, str)
        and prediction_hash != manifest_hash
    ):
        findings.append(f"{prediction_path.name}: context manifest blind_artifact_sha256 mismatch")


def _check_manifest_model_config(
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    model_config = manifest.get("model_config")
    if model_config is not None and (
        not isinstance(model_config, dict) or not model_config
    ):
        findings.append(f"{prediction_path.name}: context manifest model_config is invalid")


def _check_manifest_news_input(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    has_news_fields = "news_file" in manifest or "news_sha256" in manifest
    if not has_news_fields:
        return
    news_file = manifest.get("news_file")
    news_sha256 = manifest.get("news_sha256")
    if not isinstance(news_file, str) or not news_file:
        findings.append(f"{prediction_path.name}: context manifest missing news_file")
        return
    news_path = _resolve_manifest_path(root, news_file)
    if news_path is None:
        findings.append(
            f"{prediction_path.name}: context manifest news_file path escapes project root: "
            f"{news_file}"
        )
        return
    if not news_path.exists():
        findings.append(f"{prediction_path.name}: context manifest news_file not found: {news_file}")
        return
    if not isinstance(news_sha256, str) or not news_sha256:
        findings.append(f"{prediction_path.name}: context manifest missing news_sha256")
    elif file_sha256(news_path) != news_sha256:
        findings.append(f"{prediction_path.name}: context manifest news_sha256 mismatch")

    _check_manifest_news_row_counts(prediction_path, manifest, news_path, findings)


def _check_manifest_news_row_counts(
    prediction_path: Path,
    manifest: dict[str, Any],
    news_path: Path,
    findings: list[str],
) -> None:
    row_fields = (
        "news_row_count",
        "included_news_row_count",
        "excluded_news_row_count",
    )
    if not any(field in manifest for field in row_fields):
        return
    values: dict[str, int] = {}
    for field in row_fields:
        value = manifest.get(field)
        if not isinstance(value, int) or value < 0:
            findings.append(f"{prediction_path.name}: context manifest {field} is invalid")
            return
        values[field] = value
    if (
        values["included_news_row_count"] + values["excluded_news_row_count"]
        != values["news_row_count"]
    ):
        findings.append(f"{prediction_path.name}: context manifest news row counts mismatch")
    if "news_window_start_at" not in manifest and "news_window_end_at" not in manifest:
        return
    trade_date = _manifest_date(manifest.get("trade_date"))
    cutoff_at = _manifest_datetime(manifest.get("cutoff_at"))
    window_start_at = _manifest_datetime(manifest.get("news_window_start_at"))
    window_end_at = _manifest_datetime(manifest.get("news_window_end_at"))
    if trade_date is None:
        findings.append(f"{prediction_path.name}: context manifest trade_date is invalid")
        return
    if cutoff_at is None:
        findings.append(f"{prediction_path.name}: context manifest cutoff_at is invalid")
        return
    if window_start_at is None:
        findings.append(f"{prediction_path.name}: context manifest news_window_start_at is invalid")
        return
    if window_end_at is None:
        findings.append(f"{prediction_path.name}: context manifest news_window_end_at is invalid")
        return
    if window_start_at != default_news_window_start(trade_date):
        findings.append(f"{prediction_path.name}: context manifest news_window_start_at mismatch")
    if window_end_at != cutoff_at:
        findings.append(f"{prediction_path.name}: context manifest news_window_end_at mismatch")
    try:
        batch = load_news_csv(news_path, trade_date=trade_date)
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        findings.append(
            f"{prediction_path.name}: context manifest news_file invalid CSV: {type(exc).__name__}"
        )
        return
    observed_included = sum(
        1 for item in batch.items if window_start_at <= item.published_at <= window_end_at
    )
    observed_excluded = batch.row_count - observed_included
    if batch.row_count != values["news_row_count"]:
        findings.append(f"{prediction_path.name}: context manifest news_row_count mismatch")
    if observed_included != values["included_news_row_count"]:
        findings.append(f"{prediction_path.name}: context manifest included_news_row_count mismatch")
    if observed_excluded != values["excluded_news_row_count"]:
        findings.append(f"{prediction_path.name}: context manifest excluded_news_row_count mismatch")
    row_summary = manifest.get("row_disposition_summary")
    expected_missing = (
        row_summary.get("missing_collected_at") if isinstance(row_summary, dict) else None
    )
    if isinstance(expected_missing, int):
        observed_missing = sum(1 for item in batch.items if item.collected_at is None)
        if observed_missing != expected_missing:
            findings.append(
                f"{prediction_path.name}: context manifest missing_collected_at mismatch"
            )


def _manifest_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _manifest_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _check_manifest_context_file_hashes(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    _check_manifest_file_hashes(
        root,
        prediction_path,
        manifest,
        files_field="brain_files",
        hashes_field="brain_file_hashes",
        label="brain file",
        findings=findings,
    )
    _check_manifest_file_hashes(
        root,
        prediction_path,
        manifest,
        files_field="shard_brain_files",
        hashes_field="shard_brain_file_hashes",
        label="shard brain file",
        findings=findings,
    )


def _check_manifest_memory_sweep_artifacts(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    raw_artifacts = manifest.get("memory_sweep_artifacts")
    if raw_artifacts is None:
        return
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, str) and item for item in raw_artifacts
    ):
        findings.append(
            f"{prediction_path.name}: context manifest memory_sweep_artifacts is invalid"
        )
        return
    artifact_refs = [str(item) for item in raw_artifacts]
    if not artifact_refs:
        return
    raw_hashes = manifest.get("memory_sweep_artifact_hashes")
    hashes: dict[str, str] = {}
    if (
        not isinstance(raw_hashes, dict)
        or not raw_hashes
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_hashes.items()
        )
    ):
        findings.append(
            f"{prediction_path.name}: context manifest memory_sweep_artifact_hashes is invalid"
        )
    else:
        hashes = {str(key): str(value) for key, value in raw_hashes.items()}

    if len(artifact_refs) != len(set(artifact_refs)):
        findings.append(f"{prediction_path.name}: context manifest duplicate memory sweep artifact")
    missing_hashes = sorted(set(artifact_refs) - set(hashes))
    extra_hashes = sorted(set(hashes) - set(artifact_refs))
    if missing_hashes:
        findings.append(
            f"{prediction_path.name}: context manifest missing memory_sweep_artifact_hashes: "
            f"{', '.join(missing_hashes)}"
        )
    if extra_hashes:
        findings.append(
            f"{prediction_path.name}: context manifest unlisted memory_sweep_artifact_hashes: "
            f"{', '.join(extra_hashes)}"
        )

    expected_mode = manifest.get("mode")
    expected_trade_date = manifest.get("trade_date")
    expected_cutoff_at = manifest.get("cutoff_at")
    expected_brain_version = manifest.get("brain_version")
    observed_episode_ids: list[str] = []
    observed_cache_hits = 0
    for artifact_ref in artifact_refs:
        artifact_path = _resolve_manifest_path(root, artifact_ref)
        if artifact_path is None:
            findings.append(
                f"{prediction_path.name}: context manifest memory sweep artifact path "
                f"escapes project root: {artifact_ref}"
            )
            continue
        if not artifact_path.exists():
            findings.append(
                f"{prediction_path.name}: context manifest memory sweep artifact not found: "
                f"{artifact_ref}"
            )
            continue
        expected_hash = hashes.get(artifact_ref)
        if isinstance(expected_hash, str) and file_sha256(artifact_path) != expected_hash:
            findings.append(
                f"{prediction_path.name}: context manifest memory sweep artifact sha256 "
                f"mismatch: {artifact_ref}"
            )
        payload = _read_json_object(artifact_path, findings)
        if payload is None:
            continue
        if payload.get("schema_version") != "nslab.memory_sweep_contribution.v1":
            findings.append(
                f"{prediction_path.name}: memory sweep artifact schema mismatch: "
                f"{artifact_ref}"
            )
        for field, expected in (
            ("mode", expected_mode),
            ("trade_date", expected_trade_date),
            ("cutoff_at", expected_cutoff_at),
            ("brain_version", expected_brain_version),
        ):
            if expected is not None and payload.get(field) != expected:
                findings.append(
                    f"{prediction_path.name}: memory sweep artifact {field} mismatch: "
                    f"{artifact_ref}"
                )
        episode_ids = payload.get("episode_ids")
        if not isinstance(episode_ids, list) or not all(
            isinstance(episode_id, str) for episode_id in episode_ids
        ):
            findings.append(
                f"{prediction_path.name}: memory sweep artifact episode_ids invalid: "
                f"{artifact_ref}"
            )
            continue
        observed_episode_ids.extend(episode_ids)
        if payload.get("episode_count") != len(episode_ids):
            findings.append(
                f"{prediction_path.name}: memory sweep artifact episode_count mismatch: "
                f"{artifact_ref}"
            )
        if payload.get("from_cache") is True:
            observed_cache_hits += 1

    expected_shard_count = manifest.get("memory_sweep_shard_count")
    if isinstance(expected_shard_count, int) and expected_shard_count != len(artifact_refs):
        findings.append(f"{prediction_path.name}: context manifest memory_sweep_shard_count mismatch")
    expected_cache_hits = manifest.get("memory_sweep_cache_hits")
    if isinstance(expected_cache_hits, int) and expected_cache_hits != observed_cache_hits:
        findings.append(f"{prediction_path.name}: context manifest memory_sweep_cache_hits mismatch")
    expected_swept_ids = manifest.get("swept_episode_ids")
    if isinstance(expected_swept_ids, list) and all(
        isinstance(episode_id, str) for episode_id in expected_swept_ids
    ):
        if Counter(observed_episode_ids) != Counter(expected_swept_ids):
            findings.append(
                f"{prediction_path.name}: context manifest memory_sweep swept episode ids mismatch"
            )
    else:
        findings.append(f"{prediction_path.name}: context manifest swept_episode_ids is invalid")


def _check_manifest_output_artifacts(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    _check_manifest_prediction_artifact(root, prediction_path, manifest, findings)
    _check_manifest_report_artifact(root, prediction_path, manifest, findings)
    _check_manifest_jsonl_artifact(
        root,
        prediction_path,
        manifest,
        artifact_field="row_disposition_artifact",
        sha_field="row_disposition_sha256",
        count_field=None,
        expected_schema="nslab.row_disposition.v1",
        label="row_disposition",
        findings=findings,
    )
    source_ledger_rows = _check_manifest_jsonl_artifact(
        root,
        prediction_path,
        manifest,
        artifact_field="source_ledger_artifact",
        sha_field="source_ledger_sha256",
        count_field="source_ledger_entry_count",
        expected_schema="nslab.source_ledger.v1",
        label="source_ledger",
        findings=findings,
    )
    if source_ledger_rows is not None:
        _check_source_ledger_source_coverage(
            prediction_path,
            manifest,
            source_ledger_rows,
            findings,
        )
    event_cluster_rows = _check_manifest_jsonl_artifact(
        root,
        prediction_path,
        manifest,
        artifact_field="event_cluster_artifact",
        sha_field="event_cluster_sha256",
        count_field="event_cluster_count",
        expected_schema="nslab.news_event_cluster.v1",
        label="event_cluster",
        findings=findings,
    )
    if event_cluster_rows is not None:
        _check_event_cluster_artifact_summary(
            prediction_path,
            manifest,
            event_cluster_rows,
            findings,
        )
    _check_news_novelty_review_artifact(root, prediction_path, manifest, findings)
    _check_semantic_retrieval_plan_artifact(root, prediction_path, manifest, findings)
    semantic_retrieval_rows = _check_manifest_jsonl_artifact(
        root,
        prediction_path,
        manifest,
        artifact_field="semantic_retrieval_artifact",
        sha_field="semantic_retrieval_sha256",
        count_field="semantic_retrieval_query_count",
        expected_schema="nslab.semantic_retrieval_result.v1",
        label="semantic_retrieval",
        findings=findings,
    )
    if semantic_retrieval_rows is not None:
        _check_semantic_retrieval_artifact_summary(
            prediction_path,
            manifest,
            semantic_retrieval_rows,
            findings,
        )
    _check_candidate_expansion_artifact(root, prediction_path, manifest, findings)
    _check_candidate_web_check_artifacts(root, prediction_path, manifest, findings)
    _check_candidate_verification_artifact(root, prediction_path, manifest, findings)
    _check_manifest_final_synthesis_context_artifact(root, prediction_path, manifest, findings)


def _check_manifest_jsonl_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    *,
    artifact_field: str,
    sha_field: str,
    count_field: str | None,
    expected_schema: str,
    label: str,
    findings: list[str],
) -> list[dict[str, Any]] | None:
    artifact_ref = manifest.get(artifact_field)
    expected_hash = manifest.get(sha_field)
    if artifact_ref is None and expected_hash is None:
        return None
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label=artifact_field,
        findings=findings,
    )
    if artifact_path is None:
        return None
    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(f"{prediction_path.name}: context manifest missing {sha_field}")
    elif sha256_text(text) != expected_hash:
        findings.append(f"{prediction_path.name}: context manifest {sha_field} mismatch")

    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{line_number} invalid JSON"
            )
            continue
        if not isinstance(row, dict):
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{line_number} "
                "is not an object"
            )
            continue
        rows.append(row)
        if row.get("schema_version") != expected_schema:
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{line_number} "
                "schema_version mismatch"
            )
        run_id = manifest.get("run_id")
        if isinstance(run_id, str) and row.get("run_id") != run_id:
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{line_number} "
                "run_id mismatch"
            )

    if count_field is not None:
        expected_count = manifest.get(count_field)
        if isinstance(expected_count, int) and not isinstance(expected_count, bool):
            if expected_count != len(rows):
                findings.append(
                    f"{prediction_path.name}: context manifest {label} count mismatch"
                )
        elif expected_count is not None:
            findings.append(
                f"{prediction_path.name}: context manifest {count_field} invalid"
            )

    if label == "row_disposition":
        row_summary = manifest.get("row_disposition_summary")
        if isinstance(row_summary, dict):
            total_rows = row_summary.get("total_rows")
            if (
                isinstance(total_rows, int)
                and not isinstance(total_rows, bool)
                and total_rows != len(rows)
            ):
                findings.append(
                    f"{prediction_path.name}: context manifest row_disposition "
                    "count mismatch"
                )
    return rows


def _check_source_ledger_source_coverage(
    prediction_path: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    _check_source_ledger_summary(prediction_path, manifest, rows, findings)

    web_source_ids = _unique_strings(
        row.get("source_id")
        for row in rows
        if row.get("source_type") == "web_search_result"
    )
    if not _same_unique_string_set(web_source_ids, _string_list(manifest.get("web_sources"))):
        findings.append(
            f"{prediction_path.name}: context manifest source_ledger "
            "web_sources mismatch"
        )

    candidate_web_source_ids = _unique_strings(
        row.get("source_id")
        for row in rows
        if row.get("source_type") == "candidate_web_check"
    )
    if candidate_web_source_ids != _string_list(manifest.get("candidate_web_source_ids")):
        findings.append(
            f"{prediction_path.name}: context manifest source_ledger "
            "candidate_web_source_ids mismatch"
        )

    excluded_source_ids = {
        *_string_list(manifest.get("excluded_web_source_ids")),
        *_string_list(manifest.get("excluded_candidate_web_source_ids")),
    }
    ledger_source_ids = {
        source_id
        for row in rows
        if isinstance(source_id := row.get("source_id"), str)
    }
    if ledger_source_ids & excluded_source_ids:
        findings.append(
            f"{prediction_path.name}: context manifest source_ledger "
            "contains excluded source_id"
        )


def _check_source_ledger_summary(
    prediction_path: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    summary = manifest.get("source_ledger_summary")
    if summary is None:
        return
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest source_ledger_summary invalid"
        )
        return
    phase_counts = Counter(
        row.get("usage_phase")
        for row in rows
        if isinstance(row.get("usage_phase"), str)
    )
    expected_counts = {
        "total_sources": len(rows),
        "blind_sources": phase_counts.get("BLIND", 0),
        "outcome_sources": phase_counts.get("OUTCOME", 0),
        "postmortem_sources": phase_counts.get("POSTMORTEM", 0),
    }
    if any(summary.get(key) != value for key, value in expected_counts.items()):
        findings.append(
            f"{prediction_path.name}: context manifest source_ledger_summary mismatch"
        )


def _check_event_cluster_artifact_summary(
    prediction_path: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    summary = manifest.get("event_cluster_summary")
    if summary is None:
        return
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest event_cluster_summary invalid"
        )
        return

    _check_summary_int(
        prediction_path,
        summary,
        "cluster_count",
        len(rows),
        label="event_cluster",
        findings=findings,
    )
    source_row_count = sum(_non_bool_int(row.get("row_count")) or 0 for row in rows)
    _check_summary_int(
        prediction_path,
        summary,
        "source_row_count",
        source_row_count,
        label="event_cluster",
        findings=findings,
    )
    exact_duplicate_count = sum(
        _non_bool_int(row.get("exact_duplicate_count")) or 0 for row in rows
    )
    _check_summary_int(
        prediction_path,
        summary,
        "exact_duplicate_count",
        exact_duplicate_count,
        label="event_cluster",
        findings=findings,
    )
    exact_duplicate_cluster_count = sum(
        1 for row in rows if (_non_bool_int(row.get("exact_duplicate_count")) or 0) > 0
    )
    _check_summary_int(
        prediction_path,
        summary,
        "exact_duplicate_cluster_count",
        exact_duplicate_cluster_count,
        label="event_cluster",
        findings=findings,
    )
    methods = {
        method
        for row in rows
        if isinstance(method := row.get("cluster_method"), str) and method
    }
    summary_method = summary.get("cluster_method")
    if isinstance(summary_method, str) and methods and methods != {summary_method}:
        findings.append(
            f"{prediction_path.name}: context manifest event_cluster "
            "cluster_method mismatch"
        )
    for index, row in enumerate(rows, start=1):
        row_count = _non_bool_int(row.get("row_count"))
        if row_count is None:
            findings.append(
                f"{prediction_path.name}: context manifest event_cluster:{index} "
                "row_count invalid"
            )
            continue
        for field in ("row_numbers", "event_ids", "source_ids"):
            value = row.get(field)
            if isinstance(value, list) and len(value) != row_count:
                findings.append(
                    f"{prediction_path.name}: context manifest event_cluster:{index} "
                    f"{field} count mismatch"
                )


def _check_news_novelty_review_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("news_novelty_review_artifact")
    expected_hash = manifest.get("news_novelty_review_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="news_novelty_review_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing "
            "news_novelty_review_sha256"
        )
    elif sha256_text(text) != expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest "
            "news_novelty_review_sha256 mismatch"
        )
    payload = _read_json_object(artifact_path, findings)
    if payload is None:
        return
    if payload.get("schema_version") != "nslab.news_novelty_review.v1":
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "schema_version mismatch"
        )
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("run_id") != run_id:
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "run_id mismatch"
        )
    prompt_hash = _manifest_prompt_hash(manifest, "news_novelty_review")
    if isinstance(prompt_hash, str) and payload.get("prompt_sha256") != prompt_hash:
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "prompt_hash mismatch"
        )
    _check_news_novelty_review_counts(prediction_path, manifest, payload, findings)


def _check_news_novelty_review_counts(
    prediction_path: Path,
    manifest: dict[str, Any],
    payload: dict[str, Any],
    findings: list[str],
) -> None:
    findings_rows = payload.get("findings")
    if not isinstance(findings_rows, list) or not all(
        isinstance(item, dict) for item in findings_rows
    ):
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "findings invalid"
        )
        return
    manifest_count = manifest.get("news_novelty_review_count")
    manifest_count_int = _non_bool_int(manifest_count)
    if manifest_count_int is not None and manifest_count_int != len(findings_rows):
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "count mismatch"
        )
    elif manifest_count is not None and manifest_count_int is None:
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review_count "
            "invalid"
        )
    _check_payload_int(
        prediction_path,
        payload,
        "reviewed_cluster_count",
        len(findings_rows),
        label="news_novelty_review",
        findings=findings,
    )
    event_cluster_count = manifest.get("event_cluster_count")
    if isinstance(event_cluster_count, int) and not isinstance(event_cluster_count, bool):
        _check_payload_int(
            prediction_path,
            payload,
            "cluster_count",
            event_cluster_count,
            label="news_novelty_review",
            findings=findings,
        )
    summary = manifest.get("news_novelty_review_summary")
    if summary is None:
        return
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest "
            "news_novelty_review_summary invalid"
        )
        return
    _check_summary_int(
        prediction_path,
        summary,
        "cluster_count",
        _non_bool_int(payload.get("cluster_count")),
        label="news_novelty_review",
        findings=findings,
    )
    _check_summary_int(
        prediction_path,
        summary,
        "reviewed_cluster_count",
        len(findings_rows),
        label="news_novelty_review",
        findings=findings,
    )
    time_verified_count = sum(
        1 for item in findings_rows if item.get("time_verified") is True
    )
    _check_summary_int(
        prediction_path,
        summary,
        "time_verified_count",
        time_verified_count,
        label="news_novelty_review",
        findings=findings,
    )
    excluded_ids = payload.get("excluded_after_cutoff_source_ids")
    if isinstance(excluded_ids, list):
        _check_summary_int(
            prediction_path,
            summary,
            "excluded_after_cutoff_source_count",
            len(excluded_ids),
            label="news_novelty_review",
            findings=findings,
        )
    _check_news_novelty_counts_summary(
        prediction_path,
        summary,
        findings_rows,
        findings,
    )


def _check_news_novelty_counts_summary(
    prediction_path: Path,
    summary: dict[str, Any],
    findings_rows: list[Any],
    findings: list[str],
) -> None:
    summary_counts = summary.get("novelty_counts")
    if summary_counts is None:
        return
    if not isinstance(summary_counts, dict):
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "novelty_counts invalid"
        )
        return
    observed_counts = Counter(
        novelty
        for item in findings_rows
        if isinstance(item, dict)
        and isinstance(novelty := item.get("novelty"), str)
        and novelty
    )
    invalid_count = False
    for novelty, expected_count in summary_counts.items():
        if not isinstance(novelty, str) or _non_bool_int(expected_count) is None:
            invalid_count = True
            break
        if int(expected_count) != observed_counts.get(novelty, 0):
            findings.append(
                f"{prediction_path.name}: context manifest news_novelty_review "
                "novelty_counts mismatch"
            )
            return
    if invalid_count or any(novelty not in summary_counts for novelty in observed_counts):
        findings.append(
            f"{prediction_path.name}: context manifest news_novelty_review "
            "novelty_counts mismatch"
        )


def _check_semantic_retrieval_plan_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("semantic_retrieval_plan_artifact")
    expected_hash = manifest.get("semantic_retrieval_plan_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="semantic_retrieval_plan_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing "
            "semantic_retrieval_plan_sha256"
        )
    elif sha256_text(text) != expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest "
            "semantic_retrieval_plan_sha256 mismatch"
        )
    payload = _read_json_object(artifact_path, findings)
    if payload is None:
        return
    if payload.get("schema_version") != "nslab.semantic_retrieval_plan.v1":
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "schema_version mismatch"
        )
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("run_id") != run_id:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "run_id mismatch"
        )
    prompt_hash = _manifest_prompt_hash(manifest, "semantic_retrieval_plan")
    if isinstance(prompt_hash, str) and payload.get("prompt_sha256") != prompt_hash:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "prompt_hash mismatch"
        )
    expected_categories = _semantic_retrieval_required_categories(manifest)
    observed_categories = _string_list(payload.get("required_categories"))
    if expected_categories and observed_categories != expected_categories:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "required_categories mismatch"
        )
    queries = payload.get("queries")
    if not isinstance(queries, list) or not all(isinstance(item, dict) for item in queries):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "queries invalid"
        )
        return
    expected_query_count = _non_bool_int(manifest.get("semantic_retrieval_query_count"))
    if expected_query_count is not None and len(queries) != expected_query_count:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "query_count mismatch"
        )
    query_categories = [
        category
        for query in queries
        if isinstance(category := query.get("category"), str) and category
    ]
    if len(query_categories) != len(queries):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "query categories invalid"
        )
    if expected_categories and set(query_categories) != set(expected_categories):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "category coverage mismatch"
        )
    if any(
        not isinstance(query.get("query"), str) or not query.get("query")
        for query in queries
    ):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_plan "
            "query text invalid"
        )


def _check_semantic_retrieval_artifact_summary(
    prediction_path: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    category_counts = Counter(
        category
        for row in rows
        if isinstance(category := row.get("category"), str) and category
    )
    summary = manifest.get("semantic_retrieval_summary")
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_summary invalid"
        )
        return
    expected_category_counts = summary.get("category_query_counts")
    if isinstance(expected_category_counts, dict):
        expected_counts = {
            key: value
            for key, value in expected_category_counts.items()
            if isinstance(key, str) and _non_bool_int(value) is not None
        }
        if len(expected_counts) != len(expected_category_counts) or dict(category_counts) != expected_counts:
            findings.append(
                f"{prediction_path.name}: context manifest semantic_retrieval "
                "category_counts mismatch"
            )
    else:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "category_counts invalid"
        )
    included_ids = _unique_strings(
        episode_id
        for row in rows
        for episode_id in _string_list(row.get("included_episode_ids"))
    )
    excluded_ids = _unique_strings(
        episode_id
        for row in rows
        for episode_id in _string_list(row.get("excluded_episode_ids"))
    )
    expected_included_ids = _string_list(manifest.get("semantic_retrieval_episode_ids"))
    expected_excluded_ids = _string_list(
        manifest.get("excluded_semantic_retrieval_episode_ids")
    )
    if included_ids != expected_included_ids:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "included_episode_ids mismatch"
        )
    if excluded_ids != expected_excluded_ids:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "excluded_episode_ids mismatch"
        )
    _check_summary_int(
        prediction_path,
        summary,
        "query_count",
        len(rows),
        label="semantic_retrieval",
        findings=findings,
    )
    _check_summary_int(
        prediction_path,
        summary,
        "included_episode_count",
        len(included_ids),
        label="semantic_retrieval",
        findings=findings,
    )
    _check_summary_int(
        prediction_path,
        summary,
        "excluded_episode_count",
        len(excluded_ids),
        label="semantic_retrieval",
        findings=findings,
    )
    if summary.get("retrieval_zero_is_valid") is not True:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "zero_policy missing"
        )
    expected_categories = _semantic_retrieval_required_categories(manifest)
    if expected_categories and set(category_counts) != set(expected_categories):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "category coverage mismatch"
        )
    for index, row in enumerate(rows, start=1):
        _check_semantic_retrieval_row(prediction_path, index, row, findings)


def _check_semantic_retrieval_row(
    prediction_path: Path,
    index: int,
    row: dict[str, Any],
    findings: list[str],
) -> None:
    query = row.get("query")
    if not isinstance(query, str) or not query:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval:{index} "
            "query invalid"
        )
    elif row.get("query_sha256") != sha256_text(query):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval:{index} "
            "query_sha256 mismatch"
        )
    included_ids = _string_list(row.get("included_episode_ids"))
    excluded_ids = _string_list(row.get("excluded_episode_ids"))
    result_count = _non_bool_int(row.get("result_count"))
    excluded_count = _non_bool_int(row.get("excluded_count"))
    if result_count is not None and result_count != len(included_ids):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval:{index} "
            "result_count mismatch"
        )
    if excluded_count is not None and excluded_count != len(excluded_ids):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval:{index} "
            "excluded_count mismatch"
        )


def _check_candidate_expansion_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("candidate_expansion_artifact")
    expected_hash = manifest.get("candidate_expansion_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="candidate_expansion_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing candidate_expansion_sha256"
        )
    elif sha256_text(text) != expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion_sha256 mismatch"
        )
    payload = _read_json_object(artifact_path, findings)
    if payload is None:
        return
    if payload.get("schema_version") != "nslab.candidate_expansion.v1":
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion "
            "schema_version mismatch"
        )
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("run_id") != run_id:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion run_id mismatch"
        )
    prompt_hash = _manifest_prompt_hash(manifest, "candidate_expansion")
    if isinstance(prompt_hash, str) and payload.get("prompt_sha256") != prompt_hash:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion "
            "prompt_hash mismatch"
        )
    _check_candidate_expansion_counts(prediction_path, manifest, payload, findings)


def _check_candidate_expansion_counts(
    prediction_path: Path,
    manifest: dict[str, Any],
    payload: dict[str, Any],
    findings: list[str],
) -> None:
    summary = manifest.get("candidate_expansion_summary")
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion_summary invalid"
        )
        return
    required_paths = _string_list(summary.get("required_paths"))
    observed_required_paths = _string_list(payload.get("required_paths"))
    if required_paths and observed_required_paths != required_paths:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion "
            "required_paths mismatch"
        )
    findings_rows = payload.get("findings")
    if not isinstance(findings_rows, list) or not all(
        isinstance(item, dict) for item in findings_rows
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion findings invalid"
        )
        return
    _check_summary_int(
        prediction_path,
        summary,
        "finding_count",
        len(findings_rows),
        label="candidate_expansion",
        findings=findings,
    )
    manifest_count = manifest.get("candidate_expansion_count")
    manifest_count_int = _non_bool_int(manifest_count)
    if manifest_count_int is not None and manifest_count_int != len(findings_rows):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion count mismatch"
        )
    elif manifest_count is not None and manifest_count_int is None:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion_count invalid"
        )
    observed_paths = [
        path for row in findings_rows if isinstance(path := row.get("path"), str) and path
    ]
    if len(observed_paths) != len(findings_rows):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion path invalid"
        )
    if required_paths and set(observed_paths) != set(required_paths):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion "
            "path coverage mismatch"
        )
    observed_path_counts = dict(Counter(observed_paths))
    expected_path_counts = summary.get("path_counts")
    if isinstance(expected_path_counts, dict):
        expected_counts = {
            key: value
            for key, value in expected_path_counts.items()
            if isinstance(key, str) and _non_bool_int(value) is not None
        }
        if len(expected_counts) != len(expected_path_counts) or observed_path_counts != expected_counts:
            findings.append(
                f"{prediction_path.name}: context manifest candidate_expansion "
                "path_counts mismatch"
            )
    else:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion "
            "path_counts invalid"
        )
    candidate_names = {
        candidate
        for row in findings_rows
        for candidate in _string_list(row.get("candidate_names"))
    }
    _check_summary_int(
        prediction_path,
        summary,
        "candidate_name_count",
        len(candidate_names),
        label="candidate_expansion",
        findings=findings,
    )
    web_discovery_count = sum(
        1 for row in findings_rows if row.get("requires_web_company_discovery") is True
    )
    _check_summary_int(
        prediction_path,
        summary,
        "requires_web_company_discovery_count",
        web_discovery_count,
        label="candidate_expansion",
        findings=findings,
    )
    continuation_rows = [
        row for row in findings_rows if row.get("path") == "CONTINUATION"
    ]
    continuation_verified = bool(continuation_rows) and all(
        row.get("d_minus_one_market_data_only") is True for row in continuation_rows
    )
    if summary.get("continuation_d_minus_one_only_verified") != continuation_verified:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion "
            "continuation_d_minus_one mismatch"
        )
    for index, row in enumerate(findings_rows, start=1):
        _check_candidate_expansion_row(prediction_path, index, row, findings)


def _check_candidate_expansion_row(
    prediction_path: Path,
    index: int,
    row: dict[str, Any],
    findings: list[str],
) -> None:
    for field in (
        "candidate_names",
        "sector_hypotheses",
        "investigation_questions",
        "evidence_source_ids",
        "related_cluster_ids",
        "memory_episode_ids",
        "uncertainties",
    ):
        if not isinstance(row.get(field), list) or any(
            not isinstance(item, str) for item in row.get(field, [])
        ):
            findings.append(
                f"{prediction_path.name}: context manifest candidate_expansion:{index} "
                f"{field} invalid"
            )
    if not isinstance(row.get("hypothesis"), str) or not row.get("hypothesis"):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion:{index} "
            "hypothesis invalid"
        )
    if not isinstance(row.get("requires_web_company_discovery"), bool):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion:{index} "
            "requires_web_company_discovery invalid"
        )
    if not isinstance(row.get("d_minus_one_market_data_only"), bool):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_expansion:{index} "
            "d_minus_one_market_data_only invalid"
        )


def _check_candidate_web_check_artifacts(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    rows = _check_manifest_jsonl_artifact(
        root,
        prediction_path,
        manifest,
        artifact_field="candidate_web_check_artifact",
        sha_field="candidate_web_check_sha256",
        count_field="candidate_web_check_count",
        expected_schema="nslab.candidate_web_check.v1",
        label="candidate_web_check",
        findings=findings,
    )
    excluded_rows = _check_manifest_jsonl_artifact(
        root,
        prediction_path,
        manifest,
        artifact_field="excluded_candidate_web_check_artifact",
        sha_field="excluded_candidate_web_check_sha256",
        count_field="excluded_candidate_web_check_count",
        expected_schema="nslab.excluded_candidate_web_check.v1",
        label="excluded_candidate_web_check",
        findings=findings,
    )
    if rows is not None:
        _check_candidate_web_check_source_ids(
            prediction_path,
            manifest,
            rows,
            manifest_field="candidate_web_source_ids",
            label="candidate_web_check",
            findings=findings,
        )
        _check_candidate_web_check_rows(
            prediction_path,
            rows,
            label="candidate_web_check",
            required_fields=CANDIDATE_WEB_CHECK_REQUIRED_FIELDS
            | {"verification_focus"},
            findings=findings,
        )
    if excluded_rows is not None:
        _check_candidate_web_check_source_ids(
            prediction_path,
            manifest,
            excluded_rows,
            manifest_field="excluded_candidate_web_source_ids",
            label="excluded_candidate_web_check",
            findings=findings,
        )
        _check_candidate_web_check_rows(
            prediction_path,
            excluded_rows,
            label="excluded_candidate_web_check",
            required_fields=EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
            findings=findings,
        )
    if rows is not None:
        _check_candidate_web_check_summary(
            prediction_path,
            manifest,
            rows,
            excluded_rows or [],
            findings,
        )


def _check_candidate_web_check_source_ids(
    prediction_path: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    manifest_field: str,
    label: str,
    findings: list[str],
) -> None:
    row_source_ids = _unique_strings(row.get("source_id") for row in rows)
    expected_source_ids = _string_list(manifest.get(manifest_field))
    if row_source_ids != expected_source_ids:
        findings.append(
            f"{prediction_path.name}: context manifest {label} source_ids mismatch"
        )
    if len(row_source_ids) != len(rows):
        findings.append(
            f"{prediction_path.name}: context manifest {label} duplicate source_id"
        )


def _check_candidate_web_check_rows(
    prediction_path: Path,
    rows: list[dict[str, Any]],
    *,
    label: str,
    required_fields: set[str],
    findings: list[str],
) -> None:
    for index, row in enumerate(rows, start=1):
        missing = sorted(required_fields - set(row))
        if missing:
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{index} "
                "required_fields missing"
            )
        for field in ("candidate_company_name", "candidate_path_type", "source_id", "query"):
            if not isinstance(row.get(field), str) or not row.get(field):
                findings.append(
                    f"{prediction_path.name}: context manifest {label}:{index} "
                    f"{field} invalid"
                )
        if not isinstance(row.get("candidate_rank"), int) or isinstance(
            row.get("candidate_rank"), bool
        ):
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{index} "
                "candidate_rank invalid"
            )
        if not (
            isinstance(row.get("source_url"), str)
            and row.get("source_url") == row.get("url")
        ):
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{index} "
                "source_url mismatch"
            )
        if "opened_text" in row:
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{index} "
                "opened_text present"
            )
        if "body" in row or "content" in row:
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{index} "
                "body/content present"
            )
        if "verification_focus" in row and not _string_list(row.get("verification_focus")):
            findings.append(
                f"{prediction_path.name}: context manifest {label}:{index} "
                "verification_focus invalid"
            )


def _check_candidate_web_check_summary(
    prediction_path: Path,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    excluded_rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    summary = manifest.get("candidate_web_check_summary")
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_web_check_summary invalid"
        )
        return
    _check_summary_int(
        prediction_path,
        summary,
        "source_count",
        len(rows),
        label="candidate_web_check",
        findings=findings,
    )
    _check_summary_int(
        prediction_path,
        summary,
        "excluded_source_count",
        len(excluded_rows),
        label="candidate_web_check",
        findings=findings,
    )
    expected_focus = _string_list(summary.get("verification_focus"))
    if expected_focus and any(
        _string_list(row.get("verification_focus")) != expected_focus for row in rows
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_web_check "
            "verification_focus mismatch"
        )


def _check_candidate_verification_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("candidate_verification_artifact")
    expected_hash = manifest.get("candidate_verification_sha256")
    candidate_count = _non_bool_int(manifest.get("candidate_verification_count"))
    has_contract = (
        artifact_ref is not None
        or expected_hash is not None
        or bool(candidate_count)
        or bool(manifest.get("candidate_verification_summary"))
    )
    if not has_contract:
        return

    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="candidate_verification_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return

    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing "
            "candidate_verification_sha256"
        )
    elif sha256_text(text) != expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest "
            "candidate_verification_sha256 mismatch"
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification invalid JSON"
        )
        return
    if not isinstance(payload, dict):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "is not an object"
        )
        return

    if payload.get("schema_version") != "nslab.candidate_verification.v1":
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "schema_version mismatch"
        )
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("run_id") != run_id:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "run_id mismatch"
        )

    expected_dimensions = _candidate_verification_required_dimensions(manifest)
    if not expected_dimensions or _string_list(
        payload.get("required_dimensions")
    ) != expected_dimensions:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "required_dimensions mismatch"
        )

    candidate_findings = payload.get("findings")
    if not isinstance(candidate_findings, list) or not all(
        isinstance(finding, dict) for finding in candidate_findings
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "findings invalid"
        )
        return

    summary = manifest.get("candidate_verification_summary")
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest "
            "candidate_verification_summary invalid"
        )
        return

    expected_count = _non_bool_int(manifest.get("candidate_verification_count"))
    summary_count = _non_bool_int(summary.get("finding_count"))
    if (
        expected_count is None
        or expected_count != len(candidate_findings)
        or summary_count != len(candidate_findings)
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "count mismatch"
        )

    if (
        _non_bool_int(payload.get("subject_count")) != len(candidate_findings)
        or _non_bool_int(summary.get("subject_count")) != len(candidate_findings)
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "subject_count mismatch"
        )

    if not expected_dimensions or any(
        _candidate_verification_dimension_names(finding) != expected_dimensions
        for finding in candidate_findings
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "dimension_coverage mismatch"
        )

    observed_status_counts = _candidate_verification_status_counts(candidate_findings)
    if summary.get("status_counts") != observed_status_counts:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "status_counts mismatch"
        )

    _check_candidate_verification_sources(
        prediction_path,
        manifest,
        candidate_findings,
        findings,
    )
    _check_candidate_verification_summary_counts(
        prediction_path,
        summary,
        candidate_findings,
        findings,
    )


def _check_candidate_verification_sources(
    prediction_path: Path,
    manifest: dict[str, Any],
    candidate_findings: list[dict[str, Any]],
    findings: list[str],
) -> None:
    source_count = sum(
        _non_bool_int(finding.get("source_count")) or 0
        for finding in candidate_findings
    )
    excluded_source_count = sum(
        _non_bool_int(finding.get("excluded_source_count")) or 0
        for finding in candidate_findings
    )
    if (
        source_count != _non_bool_int(manifest.get("candidate_web_check_count"))
        or excluded_source_count
        != _non_bool_int(manifest.get("excluded_candidate_web_check_count"))
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "source_counts mismatch"
        )

    expected_accepted_ids = _string_list(manifest.get("candidate_web_source_ids"))
    expected_excluded_ids = _string_list(
        manifest.get("excluded_candidate_web_source_ids")
    )
    accepted_ids = _unique_strings(
        source_id
        for finding in candidate_findings
        for source_id in _string_list(finding.get("accepted_source_ids"))
    )
    excluded_ids = _unique_strings(
        source_id
        for finding in candidate_findings
        for source_id in _string_list(finding.get("excluded_source_ids"))
    )
    if accepted_ids != expected_accepted_ids:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "accepted_source_ids mismatch"
        )
    if excluded_ids != expected_excluded_ids:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "excluded_source_ids mismatch"
        )

    accepted_id_set = set(expected_accepted_ids)
    excluded_id_set = set(expected_excluded_ids)
    for index, finding in enumerate(candidate_findings, start=1):
        if any(
            source_id not in accepted_id_set
            for source_id in _string_list(finding.get("accepted_source_ids"))
        ):
            findings.append(
                f"{prediction_path.name}: context manifest "
                f"candidate_verification:{index} accepted_source_ids mismatch"
            )
        if any(
            source_id not in excluded_id_set
            for source_id in _string_list(finding.get("excluded_source_ids"))
        ):
            findings.append(
                f"{prediction_path.name}: context manifest "
                f"candidate_verification:{index} excluded_source_ids mismatch"
            )


def _check_candidate_verification_summary_counts(
    prediction_path: Path,
    summary: dict[str, Any],
    candidate_findings: list[dict[str, Any]],
    findings: list[str],
) -> None:
    expansion_count = sum(
        1
        for finding in candidate_findings
        if finding.get("subject_type") == "candidate_expansion"
    )
    if _non_bool_int(summary.get("candidate_expansion_subject_count")) != expansion_count:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "candidate_expansion_subject_count mismatch"
        )

    d_minus_one_count = sum(
        1
        for finding in candidate_findings
        if finding.get("d_minus_one_market_data_only") is True
    )
    if _non_bool_int(summary.get("d_minus_one_only_subject_count")) != d_minus_one_count:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "d_minus_one_only_subject_count mismatch"
        )


def _manifest_prompt_hash(manifest: dict[str, Any], key: str) -> str | None:
    prompt_hashes = manifest.get("prompt_hashes")
    if not isinstance(prompt_hashes, dict):
        return None
    value = prompt_hashes.get(key)
    return value if isinstance(value, str) and value else None


def _semantic_retrieval_required_categories(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("semantic_retrieval_summary")
    if not isinstance(summary, dict):
        return []
    return _string_list(summary.get("required_categories"))


def _candidate_web_verification_focus(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("candidate_web_check_summary")
    if not isinstance(summary, dict):
        return []
    return _string_list(summary.get("verification_focus"))


def _candidate_verification_required_dimensions(manifest: dict[str, Any]) -> list[str]:
    summary = manifest.get("candidate_verification_summary")
    if isinstance(summary, dict):
        required_dimensions = _string_list(summary.get("required_dimensions"))
        if required_dimensions:
            return required_dimensions
    return _candidate_web_verification_focus(manifest)


def _candidate_verification_dimension_names(finding: dict[str, Any]) -> list[str]:
    dimensions = finding.get("verification_dimensions")
    if not isinstance(dimensions, list):
        return []
    return [
        str(dimension["name"])
        for dimension in dimensions
        if isinstance(dimension, dict) and isinstance(dimension.get("name"), str)
    ]


def _candidate_verification_status_counts(
    candidate_findings: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for finding in candidate_findings:
        dimensions = finding.get("verification_dimensions")
        if not isinstance(dimensions, list):
            continue
        for dimension in dimensions:
            if not isinstance(dimension, dict) or not isinstance(
                dimension.get("status"), str
            ):
                continue
            counts[str(dimension["status"])] += 1
    return dict(counts)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _same_unique_string_set(left: Any, right: Any) -> bool:
    if not isinstance(left, list) or not isinstance(right, list):
        return False
    left_strings = _unique_strings(left)
    right_strings = _unique_strings(right)
    return (
        len(left_strings) == len(left)
        and len(right_strings) == len(right)
        and set(left_strings) == set(right_strings)
    )


def _check_payload_int(
    prediction_path: Path,
    payload: dict[str, Any],
    field: str,
    expected: int | None,
    *,
    label: str,
    findings: list[str],
) -> None:
    if expected is None:
        return
    observed = _non_bool_int(payload.get(field))
    if observed is None or observed != expected:
        findings.append(
            f"{prediction_path.name}: context manifest {label} {field} mismatch"
        )


def _check_summary_int(
    prediction_path: Path,
    summary: dict[str, Any],
    field: str,
    expected: int | None,
    *,
    label: str,
    findings: list[str],
) -> None:
    if expected is None or field not in summary:
        return
    observed = _non_bool_int(summary.get(field))
    if observed is None or observed != expected:
        findings.append(
            f"{prediction_path.name}: context manifest {label} {field} mismatch"
        )


def _non_bool_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _check_manifest_prediction_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("prediction_artifact")
    expected_hash = manifest.get("prediction_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="prediction_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(f"{prediction_path.name}: context manifest missing prediction_sha256")
    elif file_sha256(artifact_path) != expected_hash:
        findings.append(f"{prediction_path.name}: context manifest prediction_sha256 mismatch")
    payload = _read_json_object(artifact_path, findings)
    if payload is None:
        return
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("context_manifest_id") != run_id:
        findings.append(
            f"{prediction_path.name}: context manifest prediction_artifact run_id mismatch"
        )
    if payload.get("schema_version") != "nslab.blind_prediction.v1":
        findings.append(
            f"{prediction_path.name}: context manifest prediction_artifact "
            "schema_version mismatch"
        )
    sealed_at = payload.get("sealed_at")
    if not isinstance(sealed_at, str) or not sealed_at:
        findings.append(
            f"{prediction_path.name}: context manifest prediction_artifact "
            "sealed_at missing"
        )
    artifact_blind_hash = payload.get("blind_artifact_sha256")
    if not isinstance(artifact_blind_hash, str) or not artifact_blind_hash:
        findings.append(
            f"{prediction_path.name}: context manifest prediction_artifact "
            "missing blind_artifact_sha256"
        )
    else:
        expected_blind_hash = sha256_text(
            canonical_json({**payload, "blind_artifact_sha256": None})
        )
        if artifact_blind_hash != expected_blind_hash:
            findings.append(
                f"{prediction_path.name}: context manifest prediction_artifact "
                "blind_artifact_sha256 mismatch"
            )
    manifest_blind_hash = manifest.get("blind_artifact_sha256")
    if isinstance(manifest_blind_hash, str) and artifact_blind_hash != manifest_blind_hash:
        findings.append(
            f"{prediction_path.name}: context manifest prediction_artifact "
            "manifest blind_artifact_sha256 mismatch"
        )


def _check_manifest_report_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("report_artifact")
    expected_hash = manifest.get("report_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="report_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    report_text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(f"{prediction_path.name}: context manifest missing report_sha256")
    elif sha256_text(report_text) != expected_hash:
        findings.append(f"{prediction_path.name}: context manifest report_sha256 mismatch")
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and run_id not in report_text:
        findings.append(
            f"{prediction_path.name}: context manifest report_artifact missing run id"
        )
    section_status = inspect_preopen_report_sections(report_text)
    if section_status["missing"]:
        missing = ", ".join(section_status["missing"])
        findings.append(
            f"{prediction_path.name}: context manifest report_artifact missing "
            f"required sections: {missing}"
        )
    if not section_status["ordered"]:
        findings.append(
            f"{prediction_path.name}: context manifest report_artifact required "
            "sections out of order"
        )


def _check_manifest_final_synthesis_context_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("final_synthesis_context_artifact")
    expected_hash = manifest.get("final_synthesis_context_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="final_synthesis_context_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing "
            "final_synthesis_context_sha256"
        )
    elif sha256_text(artifact_path.read_text(encoding="utf-8", errors="replace")) != expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest final_synthesis_context_sha256 mismatch"
        )
    payload = _read_json_object(artifact_path, findings)
    if payload is None:
        return
    if payload.get("schema_version") != "nslab.final_synthesis_context.v1":
        findings.append(
            f"{prediction_path.name}: final_synthesis_context invalid schema_version"
        )
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("run_id") != run_id:
        findings.append(
            f"{prediction_path.name}: final_synthesis_context run_id mismatch"
        )
    context_payload = payload.get("payload")
    if not isinstance(context_payload, dict):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context payload must be object"
        )
        return
    if payload.get("payload_sha256") != sha256_text(canonical_json(context_payload)):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context payload_sha256 mismatch"
        )
    required_inputs = context_payload.get("required_inputs")
    if not isinstance(required_inputs, list) or not all(
        isinstance(item, str) for item in required_inputs
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context required_inputs invalid"
        )
    elif payload.get("required_inputs") != required_inputs:
        findings.append(
            f"{prediction_path.name}: final_synthesis_context required_inputs mismatch"
        )
    expected_summary = final_synthesis_input_summary(context_payload)
    if payload.get("input_summary") != expected_summary:
        findings.append(
            f"{prediction_path.name}: final_synthesis_context input_summary mismatch"
        )
    manifest_summary = manifest.get("final_synthesis_context_summary")
    if manifest_summary is not None and manifest_summary != payload.get("input_summary"):
        findings.append(
            f"{prediction_path.name}: context manifest final_synthesis_context_summary "
            "mismatch"
        )
    _check_final_synthesis_embedded_artifacts(
        root,
        prediction_path,
        manifest,
        context_payload,
        findings,
    )


def _check_final_synthesis_embedded_artifacts(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    findings: list[str],
) -> None:
    event_clusters = _read_optional_manifest_jsonl_rows(
        root,
        manifest.get("event_cluster_artifact"),
    )
    if event_clusters is not None and context_payload.get("event_clusters") != event_clusters:
        findings.append(
            f"{prediction_path.name}: final_synthesis_context event_clusters mismatch"
        )

    semantic_retrieval_rows = _read_optional_manifest_jsonl_rows(
        root,
        manifest.get("semantic_retrieval_artifact"),
    )
    if semantic_retrieval_rows is not None:
        _check_final_synthesis_semantic_retrieval_context(
            prediction_path,
            manifest,
            context_payload,
            semantic_retrieval_rows,
            findings,
        )

    web_source_rows = _read_web_source_context_rows(
        root,
        manifest.get("web_source_artifact"),
    )
    if web_source_rows is not None:
        _check_final_synthesis_web_research_context(
            prediction_path,
            manifest,
            context_payload,
            web_source_rows,
            findings,
        )

    candidate_verification = _read_optional_manifest_object(
        root,
        manifest.get("candidate_verification_artifact"),
    )
    if (
        candidate_verification is not None
        and context_payload.get("candidate_verification") != candidate_verification
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "candidate_verification mismatch"
        )

    candidate_web_checks = _read_candidate_web_check_context_rows(
        root,
        manifest.get("candidate_web_check_artifact"),
    )
    if (
        candidate_web_checks is not None
        and context_payload.get("candidate_web_checks") != candidate_web_checks
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "candidate_web_checks mismatch"
        )

    news_novelty_review = _read_optional_manifest_object(
        root,
        manifest.get("news_novelty_review_artifact"),
    )
    if (
        news_novelty_review is not None
        and context_payload.get("news_novelty_review") != news_novelty_review
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "news_novelty_review mismatch"
        )

    candidate_expansion = _read_optional_manifest_object(
        root,
        manifest.get("candidate_expansion_artifact"),
    )
    if (
        candidate_expansion is not None
        and context_payload.get("open_world_candidate_expansion") != candidate_expansion
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "open_world_candidate_expansion mismatch"
        )

    red_team_artifacts = manifest.get("red_team_artifacts")
    if not (
        isinstance(red_team_artifacts, list)
        and len(red_team_artifacts) == 1
        and isinstance(red_team_artifacts[0], str)
        and red_team_artifacts[0]
    ):
        return
    red_team = _read_optional_manifest_object(root, red_team_artifacts[0])
    if red_team is not None and context_payload.get("red_team_output") != red_team:
        findings.append(
            f"{prediction_path.name}: final_synthesis_context red_team_output mismatch"
        )


def _read_optional_manifest_object(root: Path, artifact_ref: object) -> dict[str, Any] | None:
    if not isinstance(artifact_ref, str) or not artifact_ref:
        return None
    artifact_path = _resolve_manifest_path(root, artifact_ref)
    if artifact_path is None or not artifact_path.exists():
        return None
    try:
        payload = read_json(artifact_path)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_optional_manifest_jsonl_rows(
    root: Path,
    artifact_ref: object,
) -> list[dict[str, Any]] | None:
    if not isinstance(artifact_ref, str) or not artifact_ref:
        return None
    artifact_path = _resolve_manifest_path(root, artifact_ref)
    if artifact_path is None or not artifact_path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        lines = artifact_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(row, dict):
            return None
        rows.append(row)
    return rows


def _check_final_synthesis_semantic_retrieval_context(
    prediction_path: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    semantic_retrieval_rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    context = context_payload.get("additional_semantic_retrieval")
    if not isinstance(context, dict):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "additional_semantic_retrieval mismatch"
        )
        return
    expected_fields = {
        "plan_artifact": manifest.get("semantic_retrieval_plan_artifact"),
        "artifact": manifest.get("semantic_retrieval_artifact"),
        "summary": manifest.get("semantic_retrieval_summary"),
        "rows": semantic_retrieval_rows,
        "excluded_episode_ids": manifest.get("excluded_semantic_retrieval_episode_ids"),
    }
    if any(context.get(field) != expected for field, expected in expected_fields.items()):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "additional_semantic_retrieval mismatch"
        )


def _check_final_synthesis_web_research_context(
    prediction_path: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    web_source_rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    context = context_payload.get("web_research")
    if not isinstance(context, dict):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context web_research mismatch"
        )
        return
    if (
        context.get("queries") != manifest.get("web_queries")
        or not _same_unique_string_set(
            context.get("included_sources"),
            manifest.get("web_sources"),
        )
        or context.get("sources") != web_source_rows
        or not _same_unique_string_set(
            context.get("excluded_after_cutoff_source_ids"),
            manifest.get("excluded_web_source_ids"),
        )
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context web_research mismatch"
        )


def _read_web_source_context_rows(
    root: Path,
    artifact_ref: object,
) -> list[dict[str, Any]] | None:
    rows = _read_optional_manifest_jsonl_rows(root, artifact_ref)
    if rows is None:
        return None
    return [_web_source_context_row(row) for row in rows]


def _web_source_context_row(row: dict[str, Any]) -> dict[str, Any]:
    context_row = {
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


def _read_candidate_web_check_context_rows(
    root: Path,
    artifact_ref: object,
) -> list[dict[str, Any]] | None:
    if not isinstance(artifact_ref, str) or not artifact_ref:
        return None
    artifact_path = _resolve_manifest_path(root, artifact_ref)
    if artifact_path is None or not artifact_path.exists():
        return None
    rows: list[dict[str, Any]] = []
    try:
        lines = artifact_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    for line in lines:
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


def _resolve_required_manifest_artifact(
    root: Path,
    prediction_path: Path,
    artifact_ref: object,
    *,
    label: str,
    findings: list[str],
) -> Path | None:
    if not isinstance(artifact_ref, str) or not artifact_ref:
        findings.append(f"{prediction_path.name}: context manifest missing {label}")
        return None
    artifact_path = _resolve_manifest_path(root, artifact_ref)
    if artifact_path is None:
        findings.append(
            f"{prediction_path.name}: context manifest {label} path escapes project root: "
            f"{artifact_ref}"
        )
        return None
    if not artifact_path.exists():
        findings.append(
            f"{prediction_path.name}: context manifest {label} not found: {artifact_ref}"
        )
        return None
    return artifact_path


def _check_manifest_file_hashes(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    *,
    files_field: str,
    hashes_field: str,
    label: str,
    findings: list[str],
) -> None:
    raw_files = manifest.get(files_field)
    if raw_files is None:
        return
    if not isinstance(raw_files, list) or not all(isinstance(item, str) for item in raw_files):
        findings.append(f"{prediction_path.name}: context manifest {files_field} is invalid")
        return
    hashes = manifest.get(hashes_field)
    if not isinstance(hashes, dict):
        findings.append(f"{prediction_path.name}: context manifest {hashes_field} is invalid")
        return
    file_refs = [item for item in raw_files if item]
    file_ref_set = set(file_refs)
    hash_key_set = {key for key in hashes if isinstance(key, str)}
    if len(file_refs) != len(file_ref_set):
        findings.append(f"{prediction_path.name}: context manifest duplicate {label}")
    missing_hashes = sorted(file_ref_set - hash_key_set)
    extra_hashes = sorted(hash_key_set - file_ref_set)
    if missing_hashes:
        findings.append(
            f"{prediction_path.name}: context manifest missing {hashes_field}: "
            f"{', '.join(missing_hashes)}"
        )
    if extra_hashes:
        findings.append(
            f"{prediction_path.name}: context manifest unlisted {hashes_field}: "
            f"{', '.join(extra_hashes)}"
        )
    for file_ref in file_refs:
        artifact_path = _resolve_manifest_path(root, file_ref)
        if artifact_path is None:
            findings.append(
                f"{prediction_path.name}: context manifest {label} path escapes project root: "
                f"{file_ref}"
            )
            continue
        if not artifact_path.exists():
            findings.append(
                f"{prediction_path.name}: context manifest {label} not found: {file_ref}"
            )
            continue
        expected_hash = hashes.get(file_ref)
        if isinstance(expected_hash, str) and file_sha256(artifact_path) != expected_hash:
            findings.append(
                f"{prediction_path.name}: context manifest {label} sha256 mismatch: "
                f"{file_ref}"
            )


def _check_prompt_hash_traces(
    root: Path,
    prediction_path: Path,
    prompt_hashes: dict[str, Any],
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    purpose_by_hash_key = {
        "blind_analysis": "daily_blind_analysis",
        "red_team_candidate_review": "red_team_candidate_review",
        "final_synthesis": "final_synthesis",
    }
    traces_by_purpose = _trace_metadata_by_purpose(root, findings)
    for hash_key, purpose in purpose_by_hash_key.items():
        manifest_hash = prompt_hashes.get(hash_key)
        if not manifest_hash or purpose not in traces_by_purpose:
            continue
        trace_metadata = traces_by_purpose[purpose]
        prompt_hashes_for_purpose = trace_metadata["prompt_hashes"]
        if manifest_hash not in prompt_hashes_for_purpose:
            findings.append(
                f"{prediction_path.name}: prompt hash has no matching trace for {purpose}"
            )
            continue
        matching_trace_records = [
            trace_record
            for trace_record in trace_metadata["trace_records"]
            if trace_record.get("prompt_sha256") == manifest_hash
        ]
        matching_model_configs = []
        for trace_record in matching_trace_records:
            _check_trace_checkpoint(
                root,
                trace_record["path"],
                trace_record["payload"],
                findings,
            )
            model_config = trace_record["payload"].get("model_config")
            if isinstance(model_config, dict):
                matching_model_configs.append(model_config)
        _check_trace_model_config_matches_manifest(
            prediction_path,
            manifest,
            purpose,
            matching_model_configs,
            findings,
        )


def _check_trace_model_config_matches_manifest(
    prediction_path: Path,
    manifest: dict[str, Any],
    purpose: str,
    trace_model_configs: list[dict[str, Any]],
    findings: list[str],
) -> None:
    manifest_model_config = manifest.get("model_config")
    if not isinstance(manifest_model_config, dict):
        return
    comparable_keys = [
        "configured_provider",
        "provider_class",
        "model",
        "embedding_model",
        "max_concurrency",
        "shard_episode_count",
    ]
    expected = {
        key: manifest_model_config[key]
        for key in comparable_keys
        if key in manifest_model_config
    }
    if not expected:
        return
    if not trace_model_configs:
        findings.append(f"{prediction_path.name}: trace model_config missing for {purpose}")
        return
    mismatch_sets = [
        [
            key
            for key, expected_value in expected.items()
            if trace_model_config.get(key) != expected_value
        ]
        for trace_model_config in trace_model_configs
    ]
    if any(not mismatches for mismatches in mismatch_sets):
        return
    best_mismatches = min(mismatch_sets, key=len)
    findings.append(
        f"{prediction_path.name}: trace model_config mismatch for {purpose}: "
        f"{', '.join(best_mismatches)}"
    )


def _check_context_manifest(
    root: Path,
    prediction_path: Path,
    context_manifest_id: object,
    findings: list[str],
) -> dict[str, Any] | None:
    if not isinstance(context_manifest_id, str) or not context_manifest_id:
        findings.append(f"{prediction_path.name}: missing context_manifest_id")
        return None
    manifest_path = root / "runs" / "manifests" / f"{context_manifest_id}.json"
    if not manifest_path.exists():
        findings.append(f"{prediction_path.name}: context manifest not found: {context_manifest_id}")
        return None
    manifest = _read_json_object(manifest_path, findings)
    if manifest is None:
        return None
    if manifest.get("run_id") != context_manifest_id:
        findings.append(f"{prediction_path.name}: context manifest run_id mismatch")
    return manifest


def _trace_metadata_by_purpose(root: Path, findings: list[str]) -> dict[str, dict[str, Any]]:
    traces: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "runs" / "traces").glob("*.json")):
        payload = _read_json_object(path, findings)
        if payload is None:
            continue
        _check_trace_payload(path, payload, findings)
        purpose = payload.get("purpose")
        trace_input = payload.get("input")
        if not isinstance(purpose, str) or not isinstance(trace_input, dict):
            continue
        prompt_sha256 = trace_input.get("prompt_sha256")
        if not isinstance(prompt_sha256, str) or not prompt_sha256:
            continue
        trace_metadata = traces.setdefault(
            purpose,
            {"prompt_hashes": set(), "model_configs": [], "trace_records": []},
        )
        trace_metadata["prompt_hashes"].add(prompt_sha256)
        trace_metadata["trace_records"].append(
            {"path": path, "payload": payload, "prompt_sha256": prompt_sha256}
        )
        model_config = payload.get("model_config")
        if isinstance(model_config, dict):
            trace_metadata["model_configs"].append(model_config)
    return traces


def _check_trace_payload(path: Path, payload: dict[str, Any], findings: list[str]) -> None:
    schema_version = payload.get("schema_version")
    if schema_version is not None and schema_version != "nslab.llm_trace.v1":
        findings.append(f"{path.name}: trace schema_version is invalid")
    operation = _string_field(path, payload, "operation", findings)
    status = _string_field(path, payload, "status", findings)
    _string_field(path, payload, "trace_id", findings)
    _string_field(path, payload, "purpose", findings)
    _string_field(path, payload, "provider", findings)
    _string_field(path, payload, "started_at", findings)
    _string_field(path, payload, "finished_at", findings)
    _string_field(path, payload, "checkpoint_id", findings, required=False)
    if operation in {"generate_text", "generate_structured"}:
        _string_field(path, payload, "prompt_version", findings)
    metadata = payload.get("metadata")
    if metadata is not None:
        if not isinstance(metadata, dict):
            findings.append(f"{path.name}: trace metadata is not an object")
        elif (
            operation in {"generate_text", "generate_structured"}
            and "prompt_version" in metadata
            and payload.get("prompt_version") != metadata.get("prompt_version")
        ):
            findings.append(f"{path.name}: trace metadata prompt_version mismatch")
    if not isinstance(payload.get("model_config"), dict) or not payload.get("model_config"):
        findings.append(f"{path.name}: trace missing model_config")
    trace_input = payload.get("input")
    if not isinstance(trace_input, dict):
        findings.append(f"{path.name}: trace input is not an object")
    else:
        expected_input_hash = sha256_text(canonical_json(trace_input))
        if payload.get("input_sha256") != expected_input_hash:
            findings.append(f"{path.name}: trace input_sha256 mismatch")
        if operation in {"generate_text", "generate_structured"} and not isinstance(
            trace_input.get("prompt_sha256"), str
        ):
            findings.append(f"{path.name}: trace input missing prompt_sha256")
        if operation == "embed" and not isinstance(trace_input.get("texts_sha256"), str):
            findings.append(f"{path.name}: trace input missing texts_sha256")
    output = payload.get("output")
    expected_output_hash = sha256_text(canonical_json(output)) if output is not None else None
    if payload.get("output_sha256") != expected_output_hash:
        findings.append(f"{path.name}: trace output_sha256 mismatch")
    if status in {"ok", "checkpoint_hit"} and output is None:
        findings.append(f"{path.name}: successful trace missing output")
    if not isinstance(payload.get("tool_calls"), list):
        findings.append(f"{path.name}: trace tool_calls is not a list")
    if not isinstance(payload.get("retries"), int):
        findings.append(f"{path.name}: trace retries is not an integer")
    token_usage = payload.get("token_usage")
    if not isinstance(token_usage, dict):
        findings.append(f"{path.name}: trace token_usage is not an object")
    else:
        if status in {"ok", "checkpoint_hit"} and not isinstance(
            token_usage.get("prompt_tokens_estimate"), int
        ):
            findings.append(f"{path.name}: trace missing prompt token estimate")
        if (
            status in {"ok", "checkpoint_hit"}
            and operation in {"generate_text", "generate_structured"}
            and not isinstance(token_usage.get("completion_tokens_estimate"), int)
        ):
            findings.append(f"{path.name}: trace missing completion token estimate")
    if status == "error" and not isinstance(payload.get("error"), dict):
        findings.append(f"{path.name}: error trace missing error details")


def _check_trace_checkpoint(
    root: Path,
    trace_path: Path,
    trace_payload: dict[str, Any],
    findings: list[str],
) -> None:
    checkpoint_id = trace_payload.get("checkpoint_id")
    trace_status = trace_payload.get("status")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        if trace_status in {"ok", "checkpoint_hit", "error"}:
            findings.append(f"{trace_path.name}: trace missing checkpoint_id")
        return
    if checkpoint_id != Path(checkpoint_id).name:
        findings.append(f"{trace_path.name}: trace checkpoint_id is invalid")
        return
    checkpoint_path = root / "runs" / "checkpoints" / "llm" / f"{checkpoint_id}.json"
    if not checkpoint_path.exists():
        findings.append(f"{trace_path.name}: trace checkpoint missing: {checkpoint_id}")
        return
    checkpoint = _read_json_object(checkpoint_path, findings)
    if checkpoint is None:
        return
    if checkpoint.get("schema_version") != "nslab.llm_checkpoint.v1":
        findings.append(f"{trace_path.name}: trace checkpoint schema_version is invalid")
    for field in ("checkpoint_id", "operation", "purpose", "provider"):
        if checkpoint.get(field) != trace_payload.get(field):
            findings.append(f"{trace_path.name}: trace checkpoint {field} mismatch")
    expected_checkpoint_status = "ok" if trace_status == "checkpoint_hit" else trace_status
    if checkpoint.get("status") != expected_checkpoint_status:
        findings.append(f"{trace_path.name}: trace checkpoint status mismatch")
    for field in ("model_config", "input", "input_sha256"):
        if checkpoint.get(field) != trace_payload.get(field):
            findings.append(f"{trace_path.name}: trace checkpoint {field} mismatch")
    if not _checkpoint_metadata_matches_trace(checkpoint, trace_payload):
        findings.append(f"{trace_path.name}: trace checkpoint metadata mismatch")
    if (
        "retries" in checkpoint
        and trace_status != "checkpoint_hit"
        and checkpoint.get("retries") != trace_payload.get("retries")
    ):
        findings.append(f"{trace_path.name}: trace checkpoint retries mismatch")
    checkpoint_input = checkpoint.get("input")
    if isinstance(checkpoint_input, dict):
        expected_input_hash = sha256_text(canonical_json(checkpoint_input))
        if checkpoint.get("input_sha256") != expected_input_hash:
            findings.append(f"{trace_path.name}: trace checkpoint input_sha256 invalid")
    checkpoint_output = checkpoint.get("output")
    expected_output_hash = (
        sha256_text(canonical_json(checkpoint_output))
        if checkpoint_output is not None
        else None
    )
    if checkpoint.get("output_sha256") != expected_output_hash:
        findings.append(f"{trace_path.name}: trace checkpoint output_sha256 invalid")
    operation = trace_payload.get("operation")
    if operation == "embed":
        trace_output = trace_payload.get("output")
        vectors_sha256 = (
            trace_output.get("vectors_sha256") if isinstance(trace_output, dict) else None
        )
        if vectors_sha256 != checkpoint.get("output_sha256"):
            findings.append(f"{trace_path.name}: trace checkpoint embedding output mismatch")
    else:
        for field in ("output", "output_sha256"):
            if checkpoint.get(field) != trace_payload.get(field):
                findings.append(f"{trace_path.name}: trace checkpoint {field} mismatch")
    trace_error = trace_payload.get("error")
    checkpoint_error = checkpoint.get("error")
    if trace_status == "error" and checkpoint_error != trace_error:
        findings.append(f"{trace_path.name}: trace checkpoint error mismatch")
    if not isinstance(checkpoint.get("updated_at"), str) or not checkpoint.get("updated_at"):
        findings.append(f"{trace_path.name}: trace checkpoint missing updated_at")


def _checkpoint_metadata_matches_trace(
    checkpoint: dict[str, Any],
    trace_payload: dict[str, Any],
) -> bool:
    checkpoint_metadata = checkpoint.get("metadata")
    trace_metadata = trace_payload.get("metadata")
    if not isinstance(checkpoint_metadata, dict):
        return trace_metadata in (None, {})
    if isinstance(trace_metadata, dict):
        return checkpoint_metadata == trace_metadata
    return all(trace_payload.get(key) == value for key, value in checkpoint_metadata.items())


def _check_training_export_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for manifest_path in sorted((root / "training_exports").glob("*/manifest.json")):
        manifest = _read_json_object(manifest_path, findings)
        if manifest is None:
            continue
        checked += 1
        _check_training_export_manifest(root, manifest_path, manifest, findings)
    return checked


def _check_analysis_bundle_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for bundle_path in sorted((root / "reports").glob("*_nslab_episode_bundle.md")):
        checked += 1
        label = _display_path(root, bundle_path)
        try:
            parsed = parse_bundle(bundle_path)
        except BundleImportError as exc:
            findings.append(f"{label}: analysis bundle invalid: {exc}")
            continue
        failed_validations = sorted(
            key for key, value in parsed.validation.items() if value is not True
        )
        for key in failed_validations:
            findings.append(f"{label}: analysis bundle validation failed: {key}")
        manifest = parsed.json_blocks.get("bundle_manifest.json")
        if not isinstance(manifest, dict):
            findings.append(f"{label}: analysis bundle manifest missing")
            continue
        if manifest.get("schema_version") != "nslab.bundle_manifest.v1":
            findings.append(f"{label}: analysis bundle manifest schema_version invalid")
        if not isinstance(manifest.get("run_id"), str) or not manifest.get("run_id"):
            findings.append(f"{label}: analysis bundle manifest run_id missing")
    return checked


def _check_training_export_manifest(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, manifest_path)
    kind = manifest_path.parent.name
    allowed_categories = KIND_TRAINING_CATEGORIES.get(kind)
    if manifest.get("schema_version") != "nslab.training_export_manifest.v1":
        findings.append(f"{label}: training export schema_version invalid")
    if manifest.get("kind") != kind:
        findings.append(f"{label}: training export kind mismatch")
    if allowed_categories is None:
        findings.append(f"{label}: training export kind unknown")
        return
    if manifest.get("required_training_categories") != REQUIRED_TRAINING_CATEGORIES:
        findings.append(f"{label}: training export required_training_categories mismatch")
    if manifest.get("training_categories") != allowed_categories:
        findings.append(f"{label}: training export training_categories mismatch")
    output_file = manifest.get("output_file")
    if not isinstance(output_file, str) or not output_file:
        findings.append(f"{label}: training export output_file missing")
        return
    if Path(output_file).is_absolute():
        findings.append(f"{label}: training export output_file must be project-relative")
    output_path = _resolve_training_export_output_path(root, output_file)
    if output_path is None:
        findings.append(f"{label}: training export output_file escapes project root")
        return
    if not output_path.exists():
        findings.append(f"{label}: training export output_file not found")
        return
    source_hashes = _training_export_source_hashes(root, label, manifest, findings)
    source_payloads = _training_export_source_payloads(root, source_hashes)
    expected_sha = manifest.get("output_sha256")
    if not isinstance(expected_sha, str) or file_sha256(output_path) != expected_sha:
        findings.append(f"{label}: training export output_sha256 mismatch")
    rows = _read_training_export_rows(output_path, label, findings)
    _check_training_export_episode_scope(
        root,
        label,
        manifest,
        rows,
        source_hashes=source_hashes,
        findings=findings,
    )
    _check_training_export_rows(
        label,
        kind,
        allowed_categories,
        rows,
        source_hashes=source_hashes,
        source_payloads=source_payloads,
        findings=findings,
    )
    _check_training_export_manifest_counts(label, kind, manifest, rows, findings)


def _training_export_source_hashes(
    root: Path,
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> dict[str, str]:
    raw = manifest.get("source_hashes")
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in raw.items()
    ):
        findings.append(f"{label}: training export source_hashes invalid")
        return {}
    source_hashes = dict(raw)
    for episode_id, expected_hash in source_hashes.items():
        accepted_path = root / "research" / "accepted" / f"{episode_id}.json"
        if not accepted_path.exists():
            findings.append(
                f"{label}: training export source episode not found: {episode_id}"
            )
            continue
        if file_sha256(accepted_path) != expected_hash:
            findings.append(f"{label}: training export source_hash mismatch: {episode_id}")
    return source_hashes


def _training_export_source_payloads(
    root: Path,
    source_hashes: dict[str, str],
) -> dict[str, dict[str, Any]]:
    payloads: dict[str, dict[str, Any]] = {}
    for episode_id in source_hashes:
        accepted_path = root / "research" / "accepted" / f"{episode_id}.json"
        if not accepted_path.exists():
            continue
        try:
            payload = read_json(accepted_path)
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads[episode_id] = payload
    return payloads


def _check_training_export_episode_scope(
    root: Path,
    label: str,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    source_hashes: dict[str, str],
    findings: list[str],
) -> None:
    accepted_hashes = _accepted_episode_hashes(root)
    accepted_ids = set(accepted_hashes)
    source_ids = set(source_hashes)
    if source_ids != accepted_ids:
        findings.append(f"{label}: training export source_hashes episode scope mismatch")

    episode_ids = _training_export_manifest_episode_ids(label, manifest, findings)
    if episode_ids is not None and set(episode_ids) != accepted_ids:
        findings.append(f"{label}: training export episode_ids mismatch")
    if manifest.get("episode_count") != len(accepted_ids):
        findings.append(f"{label}: training export episode_count mismatch")

    skipped_ids = _training_export_skipped_episode_ids(label, manifest, findings)
    if skipped_ids is None:
        skipped_ids = set()
    if not skipped_ids <= accepted_ids:
        findings.append(f"{label}: training export skipped_episode_ids mismatch")
    if manifest.get("skipped_episode_count") != len(skipped_ids):
        findings.append(f"{label}: training export skipped_episode_count mismatch")
    if manifest.get("eligible_episode_count") != len(accepted_ids) - len(skipped_ids):
        findings.append(f"{label}: training export eligible_episode_count mismatch")

    row_episode_ids = {
        episode_id
        for row in rows
        if isinstance(episode_id := row.get("episode_id"), str) and episode_id
    }
    if not row_episode_ids <= accepted_ids:
        findings.append(f"{label}: training export row episode scope mismatch")
    if row_episode_ids & skipped_ids:
        findings.append(f"{label}: training export skipped_episode row overlap")
    if row_episode_ids | skipped_ids != accepted_ids:
        findings.append(f"{label}: training export episode coverage mismatch")


def _accepted_episode_hashes(root: Path) -> dict[str, str]:
    return {
        path.stem: file_sha256(path)
        for path in sorted((root / "research" / "accepted").glob("*.json"))
    }


def _training_export_manifest_episode_ids(
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> list[str] | None:
    raw = manifest.get("episode_ids")
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        findings.append(f"{label}: training export episode_ids invalid")
        return None
    if len(raw) != len(set(raw)):
        findings.append(f"{label}: training export episode_ids duplicate")
    return list(raw)


def _training_export_skipped_episode_ids(
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> set[str] | None:
    raw = manifest.get("skipped_episodes")
    if not isinstance(raw, list):
        findings.append(f"{label}: training export skipped_episodes invalid")
        return None
    skipped_ids: list[str] = []
    invalid = False
    for item in raw:
        if not isinstance(item, dict):
            invalid = True
            continue
        episode_id = item.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id:
            invalid = True
            continue
        skipped_ids.append(episode_id)
    if invalid:
        findings.append(f"{label}: training export skipped_episodes invalid")
    if len(skipped_ids) != len(set(skipped_ids)):
        findings.append(f"{label}: training export skipped_episode_ids duplicate")
    return set(skipped_ids)


def _resolve_training_export_output_path(root: Path, output_file: str) -> Path | None:
    path = Path(output_file)
    resolved_root = root.resolve()
    resolved_path = path.resolve() if path.is_absolute() else (root / path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_path


def _read_training_export_rows(
    output_path: Path,
    manifest_label: str,
    findings: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(output_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            findings.append(f"{manifest_label}: training export row {line_number} invalid JSON")
            continue
        if not isinstance(row, dict):
            findings.append(f"{manifest_label}: training export row {line_number} is not object")
            continue
        rows.append(row)
    return rows


def _check_training_export_rows(
    label: str,
    kind: str,
    allowed_categories: list[str],
    rows: list[dict[str, Any]],
    *,
    source_hashes: dict[str, str],
    source_payloads: dict[str, dict[str, Any]],
    findings: list[str],
) -> None:
    for index, row in enumerate(rows, start=1):
        if row.get("schema_version") != "nslab.training_example.v1":
            findings.append(f"{label}: training export row {index} schema_version invalid")
        task = row.get("task")
        if not isinstance(task, str) or not task:
            findings.append(f"{label}: training export row {index} task invalid")
        category = row.get("training_category")
        if category not in allowed_categories:
            findings.append(f"{label}: training export row {index} category invalid")
        split = row.get("split")
        if not isinstance(split, str) or not split:
            findings.append(f"{label}: training export row {index} split invalid")
        elif split != _expected_training_export_split(kind, row):
            findings.append(f"{label}: training export row {index} split mismatch")
        _check_training_export_example_id(label, index, row, findings)
        source_phase = row.get("source_phase")
        hindsight_safe = row.get("hindsight_safe_for_blind_sft")
        if source_phase not in {"BLIND", "POSTMORTEM"}:
            findings.append(f"{label}: training export row {index} source_phase invalid")
        if not isinstance(hindsight_safe, bool):
            findings.append(f"{label}: training export row {index} hindsight flag invalid")
            continue
        expected_phase = "BLIND" if hindsight_safe else "POSTMORTEM"
        if source_phase != expected_phase:
            findings.append(f"{label}: training export row {index} source_phase mismatch")
        if kind in {"preference", "evals"} and hindsight_safe:
            findings.append(f"{label}: training export row {index} must be postmortem-only")
        if (
            kind == "sft"
            and source_phase == "POSTMORTEM"
            and category != "failure_correction_examples"
        ):
            findings.append(f"{label}: training export row {index} mixes postmortem into blind SFT")
        _check_training_export_row_provenance(
            label,
            index,
            row,
            source_hashes=source_hashes,
            findings=findings,
        )
        _check_training_export_blind_row_hindsight_leaks(
            label,
            index,
            kind,
            row,
            source_payloads=source_payloads,
            findings=findings,
        )


def _expected_training_export_split(kind: str, row: dict[str, Any]) -> str:
    if kind == "sft":
        return (
            "sft_postmortem"
            if row.get("training_category") == "failure_correction_examples"
            else "sft"
        )
    return kind


def _check_training_export_example_id(
    label: str,
    index: int,
    row: dict[str, Any],
    findings: list[str],
) -> None:
    example_id = row.get("example_id")
    task = row.get("task")
    split = row.get("split")
    episode_id = row.get("episode_id")
    input_payload = row.get("input")
    if (
        not isinstance(example_id, str)
        or not example_id
        or not isinstance(task, str)
        or not task
        or not isinstance(split, str)
        or not split
        or not isinstance(episode_id, str)
        or not episode_id
        or not isinstance(input_payload, dict)
    ):
        findings.append(f"{label}: training export row {index} example_id invalid")
        return
    expected = stable_id("TRN", split, task, episode_id, canonical_json(input_payload))
    if example_id != expected:
        findings.append(f"{label}: training export row {index} example_id mismatch")


def _check_training_export_blind_row_hindsight_leaks(
    label: str,
    index: int,
    kind: str,
    row: dict[str, Any],
    *,
    source_payloads: dict[str, dict[str, Any]],
    findings: list[str],
) -> None:
    if (
        kind != "sft"
        or row.get("hindsight_safe_for_blind_sft") is not True
        or row.get("source_phase") != "BLIND"
    ):
        return
    episode_id = row.get("episode_id")
    if not isinstance(episode_id, str):
        return
    source_payload = source_payloads.get(episode_id)
    if source_payload is None:
        return
    forbidden_snippets = _training_export_postmortem_snippets(source_payload)
    if not forbidden_snippets:
        return
    row_text = canonical_json({"input": row.get("input"), "output": row.get("output")})
    for snippet in forbidden_snippets:
        if snippet in row_text:
            findings.append(
                f"{label}: training export row {index} blind-safe SFT contains "
                "postmortem content"
            )
            return


def _training_export_postmortem_snippets(
    episode: dict[str, Any],
) -> list[str]:
    postmortem = episode.get("postmortem")
    if not isinstance(postmortem, dict):
        return []
    snippets: list[str] = []
    for field in ("summary", "failure_codes", "lessons"):
        _collect_training_export_hindsight_strings(postmortem.get(field), snippets)
    return sorted({snippet for snippet in snippets if len(snippet) >= 8})


def _collect_training_export_hindsight_strings(value: Any, snippets: list[str]) -> None:
    if isinstance(value, str):
        text = value.strip()
        if text:
            snippets.append(text)
    elif isinstance(value, list):
        for item in value:
            _collect_training_export_hindsight_strings(item, snippets)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_training_export_hindsight_strings(item, snippets)


def _check_training_export_row_provenance(
    label: str,
    index: int,
    row: dict[str, Any],
    *,
    source_hashes: dict[str, str],
    findings: list[str],
) -> None:
    provenance = row.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        findings.append(f"{label}: training export row {index} provenance missing")
        return
    if not all(isinstance(item, dict) for item in provenance):
        findings.append(f"{label}: training export row {index} provenance invalid")
        return
    episode_id = row.get("episode_id")
    if not isinstance(episode_id, str) or not episode_id:
        findings.append(f"{label}: training export row {index} episode_id invalid")
        return
    expected_hash = source_hashes.get(episode_id)
    if expected_hash is None:
        findings.append(f"{label}: training export row {index} source_hash missing")
        return
    expected_uri = f"research/accepted/{episode_id}.json"
    if not any(
        entry.get("source_type") == "accepted_research_episode"
        and entry.get("uri") == expected_uri
        and entry.get("content_sha256") == expected_hash
        for entry in provenance
    ):
        findings.append(f"{label}: training export row {index} accepted episode provenance mismatch")


def _check_training_export_manifest_counts(
    label: str,
    kind: str,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    if manifest.get("row_count") != len(rows):
        findings.append(f"{label}: training export row_count mismatch")
    if manifest.get("task_counts") != _training_task_counts(rows):
        findings.append(f"{label}: training export task_counts mismatch")
    if manifest.get("category_counts") != _training_category_counts(rows, kind=kind):
        findings.append(f"{label}: training export category_counts mismatch")
    if manifest.get("missing_training_categories") != _training_missing_categories(
        rows, kind=kind
    ):
        findings.append(f"{label}: training export missing_training_categories mismatch")
    if manifest.get("source_phase_counts") != _training_source_phase_counts(rows):
        findings.append(f"{label}: training export source_phase_counts mismatch")
    blind_safe_count = sum(1 for row in rows if row.get("hindsight_safe_for_blind_sft") is True)
    hindsight_count = sum(1 for row in rows if row.get("hindsight_safe_for_blind_sft") is False)
    if manifest.get("blind_safe_row_count") != blind_safe_count:
        findings.append(f"{label}: training export blind_safe_row_count mismatch")
    if manifest.get("hindsight_row_count") != hindsight_count:
        findings.append(f"{label}: training export hindsight_row_count mismatch")


def _training_task_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        task = row.get("task")
        if isinstance(task, str):
            counts[task] = counts.get(task, 0) + 1
    return counts


def _training_category_counts(rows: list[dict[str, Any]], *, kind: str) -> dict[str, int]:
    counts = dict.fromkeys(KIND_TRAINING_CATEGORIES[kind], 0)
    for row in rows:
        category = row.get("training_category")
        if isinstance(category, str):
            counts[category] = counts.get(category, 0) + 1
    return counts


def _training_missing_categories(rows: list[dict[str, Any]], *, kind: str) -> list[str]:
    counts = _training_category_counts(rows, kind=kind)
    return [category for category in KIND_TRAINING_CATEGORIES[kind] if counts.get(category, 0) == 0]


def _training_source_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        source_phase = row.get("source_phase")
        if isinstance(source_phase, str):
            counts[source_phase] = counts.get(source_phase, 0) + 1
    return counts


def _string_field(
    path: Path,
    payload: dict[str, Any],
    field: str,
    findings: list[str],
    *,
    required: bool = True,
) -> str | None:
    value = payload.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value:
        findings.append(f"{path.name}: trace missing {field}")
        return None
    return value


def _check_red_team_artifacts(
    root: Path,
    prediction_path: Path,
    prediction: dict[str, Any],
    manifest: dict[str, Any],
    prompt_hashes: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_paths = manifest.get("red_team_artifacts", [])
    if not artifact_paths:
        return
    if not isinstance(artifact_paths, list) or not all(
        isinstance(path, str) and path for path in artifact_paths
    ):
        findings.append(f"{prediction_path.name}: context manifest red_team_artifacts is invalid")
        return

    red_team_prompt_hash = prompt_hashes.get("red_team_candidate_review")
    if not red_team_prompt_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing red_team_candidate_review prompt hash"
        )
    if not prompt_hashes.get("final_synthesis"):
        findings.append(f"{prediction_path.name}: context manifest missing final_synthesis prompt hash")

    candidates = prediction.get("candidates", [])
    candidate_count = len(candidates) if isinstance(candidates, list) else None
    context_manifest_id = manifest.get("run_id")
    red_team_summary = manifest.get("red_team_summary")
    for artifact_ref in artifact_paths:
        artifact_path = _resolve_manifest_path(root, artifact_ref)
        if artifact_path is None:
            findings.append(
                f"{prediction_path.name}: red-team artifact path escapes project root: {artifact_ref}"
            )
            continue
        if not artifact_path.exists():
            findings.append(f"{prediction_path.name}: red-team artifact not found: {artifact_ref}")
            continue
        artifact = _read_json_object(artifact_path, findings)
        if artifact is None:
            continue
        _check_red_team_artifact(
            prediction_path,
            artifact_ref,
            artifact,
            context_manifest_id=context_manifest_id,
            red_team_prompt_hash=red_team_prompt_hash,
            candidate_count=candidate_count,
            red_team_summary=red_team_summary,
            findings=findings,
        )


def _check_red_team_artifact(
    prediction_path: Path,
    artifact_ref: str,
    artifact: dict[str, Any],
    *,
    context_manifest_id: object,
    red_team_prompt_hash: object,
    candidate_count: int | None,
    red_team_summary: object,
    findings: list[str],
) -> None:
    if artifact.get("schema_version") != "nslab.red_team_artifact.v1":
        findings.append(f"{prediction_path.name}: red-team artifact schema mismatch: {artifact_ref}")
    if artifact.get("run_id") != context_manifest_id:
        findings.append(f"{prediction_path.name}: red-team artifact run_id mismatch: {artifact_ref}")
    if not artifact.get("source_prediction_id"):
        findings.append(f"{prediction_path.name}: red-team artifact missing source_prediction_id")
    if red_team_prompt_hash and artifact.get("prompt_sha256") != red_team_prompt_hash:
        findings.append(f"{prediction_path.name}: red-team artifact prompt hash mismatch: {artifact_ref}")
    if candidate_count is not None and artifact.get("candidate_count") != candidate_count:
        findings.append(
            f"{prediction_path.name}: red-team artifact candidate_count mismatch: {artifact_ref}"
        )
    candidate_findings = artifact.get("candidate_findings")
    if not isinstance(candidate_findings, list):
        findings.append(f"{prediction_path.name}: red-team artifact candidate_findings is invalid")
        return
    if candidate_count is not None and len(candidate_findings) != candidate_count:
        findings.append(
            f"{prediction_path.name}: red-team artifact finding count mismatch: {artifact_ref}"
        )
    prompt_version = artifact.get("prompt_version")
    if prompt_version == "red_team.candidate_attack.v1":
        return
    required_attack_checks = artifact.get("required_attack_checks")
    if not isinstance(required_attack_checks, list) or not all(
        isinstance(item, str) and item for item in required_attack_checks
    ):
        findings.append(
            f"{prediction_path.name}: red-team artifact required_attack_checks is invalid: "
            f"{artifact_ref}"
        )
        return
    _check_red_team_summary(
        prediction_path,
        artifact_ref,
        artifact,
        candidate_findings,
        required_attack_checks,
        red_team_summary,
        findings,
    )
    for index, item in enumerate(candidate_findings, start=1):
        if not isinstance(item, dict):
            findings.append(
                f"{prediction_path.name}: red-team artifact finding {index} is invalid: "
                f"{artifact_ref}"
            )
            continue
        attack_checks = item.get("attack_checks")
        if not isinstance(attack_checks, list):
            findings.append(
                f"{prediction_path.name}: red-team artifact finding {index} "
                f"missing attack_checks: {artifact_ref}"
            )
            continue
        observed_names = [
            check.get("name")
            for check in attack_checks
            if isinstance(check, dict)
        ]
        if observed_names != required_attack_checks:
            findings.append(
                f"{prediction_path.name}: red-team artifact finding {index} "
                f"attack_checks mismatch: {artifact_ref}"
            )
        if any(
            not isinstance(check, dict) or check.get("passed_to_synthesis") is not True
            for check in attack_checks
        ):
            findings.append(
                f"{prediction_path.name}: red-team artifact finding {index} "
                f"attack_checks not passed to synthesis: {artifact_ref}"
            )
        if item.get("passed_to_synthesis") is not True:
            findings.append(
                f"{prediction_path.name}: red-team artifact finding {index} "
                f"not passed to synthesis: {artifact_ref}"
            )


def _check_red_team_summary(
    prediction_path: Path,
    artifact_ref: str,
    artifact: dict[str, Any],
    candidate_findings: list[Any],
    required_attack_checks: list[Any],
    red_team_summary: object,
    findings: list[str],
) -> None:
    if not isinstance(red_team_summary, dict):
        findings.append(f"{prediction_path.name}: context manifest red_team_summary is invalid")
        return
    artifact_candidate_count = _non_bool_int(artifact.get("candidate_count"))
    if (
        _non_bool_int(red_team_summary.get("candidate_count")) != artifact_candidate_count
        or artifact_candidate_count != len(candidate_findings)
    ):
        findings.append(
            f"{prediction_path.name}: red-team artifact summary candidate_count "
            f"mismatch: {artifact_ref}"
        )
    if _non_bool_int(red_team_summary.get("finding_count")) != len(candidate_findings):
        findings.append(
            f"{prediction_path.name}: red-team artifact summary finding_count "
            f"mismatch: {artifact_ref}"
        )

    summary_checks = _string_list(red_team_summary.get("required_attack_checks"))
    if (
        not summary_checks
        or summary_checks != required_attack_checks
        or _non_bool_int(red_team_summary.get("required_attack_check_count"))
        != len(required_attack_checks)
    ):
        findings.append(
            f"{prediction_path.name}: red-team artifact summary "
            f"required_attack_checks mismatch: {artifact_ref}"
        )

    all_passed = True
    for item in candidate_findings:
        if not isinstance(item, dict) or item.get("passed_to_synthesis") is not True:
            all_passed = False
            break
        attack_checks = item.get("attack_checks")
        if not isinstance(attack_checks, list) or any(
            not isinstance(check, dict) or check.get("passed_to_synthesis") is not True
            for check in attack_checks
        ):
            all_passed = False
            break
    if red_team_summary.get("all_findings_passed_to_synthesis") is not all_passed:
        findings.append(
            f"{prediction_path.name}: red-team artifact summary "
            f"all_findings_passed_to_synthesis mismatch: {artifact_ref}"
        )


def _resolve_manifest_path(root: Path, artifact_ref: str) -> Path | None:
    artifact_path = Path(artifact_ref)
    if artifact_path.is_absolute():
        return None
    resolved_root = root.resolve()
    resolved_path = (root / artifact_path).resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved_path


def _check_report_link(
    root: Path,
    prediction_path: Path,
    context_manifest_id: object,
    findings: list[str],
) -> None:
    report_path = root / "reports" / f"{prediction_path.stem}_preopen.md"
    if not report_path.exists():
        findings.append(f"{prediction_path.name}: matching preopen report not found")
        return
    if isinstance(context_manifest_id, str) and context_manifest_id:
        report_text = report_path.read_text(encoding="utf-8", errors="replace")
        if context_manifest_id not in report_text:
            findings.append(f"{report_path.name}: missing context manifest run id")


def _read_json_object(path: Path, findings: list[str]) -> dict[str, Any] | None:
    try:
        payload = read_json(path)
    except json.JSONDecodeError as exc:
        findings.append(f"{path.name}: invalid JSON: {exc.msg}")
        return None
    if not isinstance(payload, dict):
        findings.append(f"{path.name}: JSON root is not an object")
        return None
    return payload
