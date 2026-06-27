"""Output provenance audits."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
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
from news_scalping_lab.records.store import BrainRecordStore
from news_scalping_lab.reporting.sections import inspect_preopen_report_sections
from news_scalping_lab.research_import.bundle import (
    CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
    EXCLUDED_CANDIDATE_WEB_CHECK_REQUIRED_FIELDS,
    BundleImportError,
    parse_bundle,
)
from news_scalping_lab.research_import.semantic import (
    SEMANTIC_IMPORT_PROMPT_VERSION,
    SEMANTIC_IMPORT_REQUIRED_OUTPUT_FIELDS,
    build_semantic_import_prompt,
)
from news_scalping_lab.training import (
    KIND_TRAINING_CATEGORIES,
    RECORD_SFT_TRAINING_CATEGORIES,
    REQUIRED_TRAINING_CATEGORIES,
)
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
OPTIONAL_PREDICTION_STRING_SEQUENCE_FIELDS = (
    "prior_positive_record_ids",
    "prior_negative_record_ids",
    "memory_record_ids",
)
POSTMORTEM_STRING_SEQUENCE_FIELDS = (
    "hits",
    "misses",
    "false_positives",
    "lessons",
)
SESSION_PACK_FILES = (
    "system_instructions.md",
    "research_brain.md",
    "memory_cases.md",
    "current_news.md",
    "company_memory.md",
    "market_context.md",
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
            _check_manifest_record_sweep_artifacts(root, path, manifest, findings)
            _check_manifest_record_count_contract(root, path, manifest, findings)
            _check_manifest_record_id_availability(root, path, manifest, findings)
            _check_manifest_output_artifacts(root, path, manifest, findings)
            _check_retrieval_miss_open_world_outputs(path, prediction, manifest, findings)
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
                or sector.get("supporting_record_ids")
                or sector.get("contradicting_record_ids")
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
                or candidate.get("memory_record_ids")
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
    checked_session_pack_manifests = _check_session_pack_provenance(root, findings)
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
        "checked_session_pack_manifests": checked_session_pack_manifests,
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
            or candidate.get("memory_record_ids")
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
    for field_name in OPTIONAL_PREDICTION_STRING_SEQUENCE_FIELDS:
        if field_name in candidate:
            _check_string_list_field(
                prefix,
                field_name,
                candidate.get(field_name),
                findings,
            )


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
            root,
            label,
            evaluation_report,
            episode,
            findings,
            sealed_prediction=sealed_prediction,
            sealed_prediction_sha256=sealed_prediction_sha256,
        )


def _check_evaluation_report_payload(
    root: Path,
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
    _check_evaluation_postmortem_trace(root, label, report, findings)


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


def _check_evaluation_postmortem_trace(
    root: Path,
    label: str,
    report: dict[str, Any],
    findings: list[str],
) -> None:
    prompt_sha256 = report.get("postmortem_prompt_sha256")
    if prompt_sha256 is None:
        return
    if not isinstance(prompt_sha256, str) or not prompt_sha256:
        findings.append(f"{label}: evaluation postmortem_prompt_sha256 invalid")
        return
    prompt_version = report.get("postmortem_prompt_version")
    if prompt_version != "evaluation_postmortem.v1":
        findings.append(f"{label}: evaluation postmortem_prompt_version invalid")
    traces_by_purpose = _trace_metadata_by_purpose(root, findings)
    trace_metadata = traces_by_purpose.get("evaluation_postmortem")
    if trace_metadata is None:
        findings.append(f"{label}: evaluation postmortem prompt hash has no matching trace")
        return
    matching_trace_records = [
        trace_record
        for trace_record in trace_metadata["trace_records"]
        if trace_record.get("prompt_sha256") == prompt_sha256
    ]
    if not matching_trace_records:
        findings.append(f"{label}: evaluation postmortem prompt hash has no matching trace")
        return
    report_model_config = report.get("postmortem_model_config")
    for trace_record in matching_trace_records:
        trace_path = trace_record["path"]
        trace_payload = trace_record["payload"]
        _check_trace_checkpoint(root, trace_path, trace_payload, findings)
        trace_input = trace_payload.get("input")
        if isinstance(trace_input, dict) and trace_input.get("response_model") != "Postmortem":
            findings.append(f"{label}: evaluation postmortem trace response_model mismatch")
        if trace_payload.get("prompt_version") != prompt_version:
            findings.append(f"{label}: evaluation postmortem trace prompt_version mismatch")
        if isinstance(report_model_config, dict) and report_model_config:
            trace_model_config = trace_payload.get("model_config")
            if trace_model_config != report_model_config:
                findings.append(f"{label}: evaluation postmortem trace model_config mismatch")
        _check_evaluation_postmortem_trace_output(
            label,
            report,
            trace_payload.get("output"),
            findings,
        )


def _check_evaluation_postmortem_trace_output(
    label: str,
    report: dict[str, Any],
    trace_output: object,
    findings: list[str],
) -> None:
    report_postmortem = report.get("postmortem")
    if not isinstance(report_postmortem, dict):
        return
    if not isinstance(trace_output, dict):
        findings.append(f"{label}: evaluation postmortem trace output missing or invalid")
        return
    trace_summary = trace_output.get("summary")
    report_summary = report_postmortem.get("summary")
    if (
        isinstance(trace_summary, str)
        and trace_summary.strip()
        and trace_summary.strip() != report_summary
    ):
        findings.append(f"{label}: evaluation postmortem trace summary mismatch")
    trace_lessons = trace_output.get("lessons")
    report_lessons = report_postmortem.get("lessons")
    if isinstance(trace_lessons, list):
        cleaned_trace_lessons = [
            lesson for lesson in trace_lessons if isinstance(lesson, str) and lesson.strip()
        ]
        if cleaned_trace_lessons and cleaned_trace_lessons != report_lessons:
            findings.append(f"{label}: evaluation postmortem trace lessons mismatch")


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
    strict_entries: list[tuple[int, dict[str, Any]]] = []
    for index, entry in enumerate(_iter_provenance_entries(episode), start=1):
        if entry.get("source_type") == STRICT_IMPORT_SOURCE_TYPE:
            strict_entries.append((index, entry))
    if not strict_entries:
        return
    _check_strict_import_audit(root, label, episode, [entry for _, entry in strict_entries], findings)
    for index, entry in strict_entries:
        _check_memory_source(root, label, index, entry, findings, kind="strict import")


def _check_strict_import_audit(
    root: Path,
    label: str,
    episode: dict[str, Any],
    provenance_entries: list[dict[str, Any]],
    findings: list[str],
) -> None:
    input_audit = episode.get("input_audit")
    if not isinstance(input_audit, dict):
        findings.append(f"{label}: strict_import input_audit missing")
        return
    strict = input_audit.get("strict_import")
    if not isinstance(strict, dict):
        findings.append(f"{label}: strict_import audit missing")
        return
    source_path = _resolve_strict_source_path(root, label, strict, findings)
    source_hash: str | None = None
    source_json: object | None = None
    if source_path is not None and source_path.exists():
        source_hash = file_sha256(source_path)
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        if strict.get("source_sha256") != source_hash:
            findings.append(f"{label}: strict_import source_sha256 mismatch")
        if strict.get("source_text_sha256") != sha256_text(source_text):
            findings.append(f"{label}: strict_import source_text_sha256 mismatch")
        try:
            source_json = read_json(source_path)
        except Exception:
            findings.append(f"{label}: strict_import source_json invalid")
        if source_json is not None and strict.get("source_json_sha256") != sha256_text(
            canonical_json(source_json)
        ):
            findings.append(f"{label}: strict_import source_json_sha256 mismatch")
        if isinstance(source_json, dict):
            if strict.get("source_schema_version") != source_json.get("schema_version"):
                findings.append(f"{label}: strict_import source_schema_version mismatch")
            if strict.get("imported_episode_id") != source_json.get("episode_id"):
                findings.append(f"{label}: strict_import imported_episode_id mismatch")
    source_id = strict.get("source_id")
    known_source_ids = {
        entry.get("source_id")
        for entry in provenance_entries
        if isinstance(entry.get("source_id"), str)
    }
    if not isinstance(source_id, str) or source_id not in known_source_ids:
        findings.append(f"{label}: strict_import source_id mismatch")
    _check_strict_provenance_entries(root, label, strict, source_hash, provenance_entries, findings)


def _resolve_strict_source_path(
    root: Path,
    label: str,
    strict: dict[str, Any],
    findings: list[str],
) -> Path | None:
    source_ref = strict.get("source_path")
    if not isinstance(source_ref, str) or not source_ref:
        findings.append(f"{label}: strict_import source_path missing")
        return None
    source_path = _resolve_project_path(root, source_ref)
    if source_path is None:
        findings.append(f"{label}: strict_import source_path escapes project root")
        return None
    if not source_path.exists():
        findings.append(f"{label}: strict_import source file not found: {source_ref}")
        return None
    return source_path


def _check_strict_provenance_entries(
    root: Path,
    label: str,
    strict: dict[str, Any],
    source_hash: str | None,
    provenance_entries: list[dict[str, Any]],
    findings: list[str],
) -> None:
    source_ref = strict.get("source_path")
    for entry in provenance_entries:
        if source_hash is not None and entry.get("content_sha256") != source_hash:
            findings.append(f"{label}: strict_import provenance content_sha256 mismatch")
        uri = entry.get("uri")
        if (
            isinstance(source_ref, str)
            and isinstance(uri, str)
            and not _same_project_path(root, source_ref, uri)
        ):
            findings.append(f"{label}: strict_import provenance uri mismatch")


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
    source_ref = semantic.get("source_path")
    source_text: str | None = None
    source_hash: str | None = None
    if source_path is not None and source_path.exists():
        source_hash = file_sha256(source_path)
        source_text = source_path.read_text(encoding="utf-8", errors="replace")
        if semantic.get("source_sha256") != source_hash:
            findings.append(f"{label}: semantic_import source_sha256 mismatch")
        if semantic.get("source_text_sha256") != sha256_text(source_text):
            findings.append(f"{label}: semantic_import source_text_sha256 mismatch")

    prompt_sha256 = _check_semantic_import_prompt_hash(
        root,
        label,
        semantic,
        source_ref=source_ref,
        source_hash=source_hash,
        source_text=source_text,
        findings=findings,
    )
    _check_semantic_provenance_entries(root, label, semantic, source_hash, provenance_entries, findings)
    _check_semantic_source_segments(label, semantic, source_text, findings)
    _check_semantic_output_sources(label, episode, semantic, findings)
    _check_semantic_output_text_provenance(label, episode, semantic, findings)
    if prompt_sha256 is not None:
        _check_semantic_import_trace(root, label, episode, semantic, prompt_sha256, findings)


def _check_semantic_import_prompt_hash(
    root: Path,
    label: str,
    semantic: dict[str, Any],
    *,
    source_ref: object,
    source_hash: str | None,
    source_text: str | None,
    findings: list[str],
) -> str | None:
    if semantic.get("prompt_version") != SEMANTIC_IMPORT_PROMPT_VERSION:
        findings.append(f"{label}: semantic_import prompt_version invalid")
    prompt_sha256 = semantic.get("prompt_sha256")
    if not isinstance(prompt_sha256, str) or not prompt_sha256:
        findings.append(f"{label}: semantic_import prompt_sha256 missing or invalid")
        return None
    if isinstance(source_ref, str) and source_hash is not None and source_text is not None:
        expected_prompt = build_semantic_import_prompt(
            root=root,
            source_path=Path(source_ref),
            source_sha256=source_hash,
            text=source_text,
        )
        if prompt_sha256 != sha256_text(expected_prompt):
            findings.append(f"{label}: semantic_import prompt_sha256 mismatch")
    return prompt_sha256


def _check_semantic_import_trace(
    root: Path,
    label: str,
    episode: dict[str, Any],
    semantic: dict[str, Any],
    prompt_sha256: str,
    findings: list[str],
) -> None:
    traces_by_purpose = _trace_metadata_by_purpose(root, findings)
    trace_metadata = traces_by_purpose.get("research_import.semantic")
    if trace_metadata is None:
        findings.append(f"{label}: semantic_import prompt hash has no matching trace")
        return
    matching_trace_records = [
        trace_record
        for trace_record in trace_metadata["trace_records"]
        if trace_record.get("prompt_sha256") == prompt_sha256
    ]
    if not matching_trace_records:
        findings.append(f"{label}: semantic_import prompt hash has no matching trace")
        return

    matching_errors: list[str] = []
    for trace_record in matching_trace_records:
        trace_errors: list[str] = []
        trace_path = trace_record["path"]
        trace_payload = trace_record["payload"]
        _check_trace_checkpoint(root, trace_path, trace_payload, trace_errors)
        trace_input = trace_payload.get("input")
        if isinstance(trace_input, dict) and trace_input.get("response_model") != (
            "SemanticResearchDraft"
        ):
            trace_errors.append(
                f"{label}: semantic_import trace response_model mismatch"
            )
        if trace_payload.get("prompt_version") != semantic.get("prompt_version"):
            trace_errors.append(f"{label}: semantic_import trace prompt_version mismatch")
        if trace_payload.get("status") not in {"ok", "checkpoint_hit"}:
            trace_errors.append(f"{label}: semantic_import trace status is not successful")
        _check_semantic_trace_output_matches_episode(
            label,
            episode,
            trace_payload.get("output"),
            trace_errors,
        )
        if not trace_errors:
            return
        matching_errors.extend(trace_errors)
    findings.extend(matching_errors)


def _check_semantic_trace_output_matches_episode(
    label: str,
    episode: dict[str, Any],
    trace_output: object,
    findings: list[str],
) -> None:
    if not isinstance(trace_output, dict):
        findings.append(f"{label}: semantic_import trace output missing or invalid")
        return
    blind_analysis = episode.get("blind_analysis")
    blind_analysis = blind_analysis if isinstance(blind_analysis, dict) else {}
    expected_pairs = {
        "trade_date": episode.get("trade_date"),
        "cutoff_at": episode.get("cutoff_at"),
        "research_version": episode.get("research_version"),
        "input_news_files": episode.get("input_news_files"),
        "input_news_hashes": episode.get("input_news_hashes"),
        "price_source_snapshot": episode.get("price_source_snapshot"),
        "blind_analysis.summary": blind_analysis.get("summary"),
        "blind_analysis.open_world_mechanisms": blind_analysis.get(
            "open_world_mechanisms"
        ),
        "blind_analysis.initial_uncertainties": blind_analysis.get(
            "initial_uncertainties"
        ),
    }
    actual_pairs = {
        "trade_date": trace_output.get("trade_date"),
        "cutoff_at": trace_output.get("cutoff_at"),
        "research_version": trace_output.get("research_version"),
        "input_news_files": trace_output.get("input_news_files"),
        "input_news_hashes": trace_output.get("input_news_hashes"),
        "price_source_snapshot": trace_output.get("price_source_snapshot"),
        "blind_analysis.summary": trace_output.get("summary"),
        "blind_analysis.open_world_mechanisms": trace_output.get(
            "open_world_mechanisms"
        ),
        "blind_analysis.initial_uncertainties": trace_output.get(
            "initial_uncertainties"
        ),
    }
    if trace_output.get("available_from") is not None:
        expected_pairs["available_from"] = episode.get("available_from")
        actual_pairs["available_from"] = trace_output.get("available_from")
    for field_name, expected in expected_pairs.items():
        if actual_pairs.get(field_name) != expected:
            findings.append(
                f"{label}: semantic_import trace output {field_name} mismatch"
            )


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


def _check_semantic_output_text_provenance(
    label: str,
    episode: dict[str, Any],
    semantic: dict[str, Any],
    findings: list[str],
) -> None:
    records = semantic.get("output_text_provenance")
    if not isinstance(records, list) or not records:
        findings.append(f"{label}: semantic_import output_text_provenance missing")
        return
    if semantic.get("output_text_provenance_count") != len(records):
        findings.append(f"{label}: semantic_import output_text_provenance_count mismatch")
    if semantic.get("output_text_provenance_sha256") != sha256_text(canonical_json(records)):
        findings.append(f"{label}: semantic_import output_text_provenance_sha256 mismatch")

    known_source_ids = {
        entry.get("source_id")
        for entry in _iter_provenance_entries(episode)
        if isinstance(entry.get("source_id"), str)
    }
    source_segment_indices = _semantic_source_segment_indices(semantic)
    expected_records = _expected_semantic_output_text_records(episode)
    expected_by_key = {_semantic_output_text_key(record): record for record in expected_records}
    seen_keys: set[tuple[str, int | None, int]] = set()

    for position, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            findings.append(
                f"{label}: semantic_import output text provenance {position} is invalid"
            )
            continue
        key = _semantic_output_text_key(record)
        field_name, item_index, sentence_index = key
        if (
            not isinstance(field_name, str)
            or not field_name
            or not isinstance(sentence_index, int)
            or isinstance(sentence_index, bool)
            or sentence_index < 1
        ):
            findings.append(
                f"{label}: semantic_import output text provenance {position} key invalid"
            )
            continue
        if item_index is not None and (
            not isinstance(item_index, int) or isinstance(item_index, bool) or item_index < 1
        ):
            findings.append(
                f"{label}: semantic_import output text provenance {position} item_index invalid"
            )
            continue
        if key in seen_keys:
            findings.append(
                f"{label}: semantic_import output text provenance duplicate: {field_name}"
            )
        seen_keys.add(key)
        expected = expected_by_key.get(key)
        if expected is None:
            findings.append(
                f"{label}: semantic_import output text provenance unexpected: {field_name}"
            )
            continue
        if record.get("text_sha256") != expected.get("text_sha256"):
            findings.append(
                f"{label}: semantic_import output text provenance {field_name} "
                "text_sha256 mismatch"
            )
        if record.get("excerpt") != expected.get("excerpt"):
            findings.append(
                f"{label}: semantic_import output text provenance {field_name} "
                "excerpt mismatch"
            )
        _check_semantic_output_text_sources(
            label,
            field_name,
            record,
            known_source_ids=known_source_ids,
            source_segment_indices=source_segment_indices,
            findings=findings,
        )

    for expected in expected_records:
        key = _semantic_output_text_key(expected)
        if key not in seen_keys:
            findings.append(
                f"{label}: semantic_import output text provenance missing: "
                f"{expected['field_name']}"
            )


def _check_semantic_output_text_sources(
    label: str,
    field_name: str,
    record: dict[str, Any],
    *,
    known_source_ids: set[object],
    source_segment_indices: set[int],
    findings: list[str],
) -> None:
    source_ids = record.get("source_ids")
    if not isinstance(source_ids, list) or not source_ids:
        findings.append(
            f"{label}: semantic_import output text provenance {field_name} "
            "source_ids invalid"
        )
    else:
        for source_id in source_ids:
            if not isinstance(source_id, str) or source_id not in known_source_ids:
                findings.append(
                    f"{label}: semantic_import output text provenance {field_name} "
                    "source_id unknown"
                )
    referenced_segments = record.get("source_segment_indices")
    if not isinstance(referenced_segments, list) or not referenced_segments:
        findings.append(
            f"{label}: semantic_import output text provenance {field_name} "
            "source_segment_indices invalid"
        )
        return
    for segment_index in referenced_segments:
        if (
            not isinstance(segment_index, int)
            or isinstance(segment_index, bool)
            or segment_index not in source_segment_indices
        ):
            findings.append(
                f"{label}: semantic_import output text provenance {field_name} "
                "source_segment_index unknown"
            )


def _expected_semantic_output_text_records(episode: dict[str, Any]) -> list[dict[str, object]]:
    blind_analysis = episode.get("blind_analysis")
    blind_analysis = blind_analysis if isinstance(blind_analysis, dict) else {}
    records: list[dict[str, object]] = []
    summary = blind_analysis.get("summary")
    if isinstance(summary, str):
        records.extend(
            _semantic_text_records_for_field(
                field_name="blind_analysis.summary",
                text=summary,
            )
        )
    mechanisms = blind_analysis.get("open_world_mechanisms")
    if isinstance(mechanisms, list):
        for item_index, mechanism in enumerate(mechanisms, start=1):
            if isinstance(mechanism, str):
                records.extend(
                    _semantic_text_records_for_field(
                        field_name="blind_analysis.open_world_mechanisms",
                        text=mechanism,
                        item_index=item_index,
                    )
                )
    uncertainties = blind_analysis.get("initial_uncertainties")
    if isinstance(uncertainties, list):
        for item_index, uncertainty in enumerate(uncertainties, start=1):
            if isinstance(uncertainty, str):
                records.extend(
                    _semantic_text_records_for_field(
                        field_name="blind_analysis.initial_uncertainties",
                        text=uncertainty,
                        item_index=item_index,
                    )
                )
    blind_predictions = episode.get("blind_predictions")
    if isinstance(blind_predictions, list):
        for item_index, candidate in enumerate(blind_predictions, start=1):
            if not isinstance(candidate, dict):
                continue
            for field_name in (
                "thesis",
                "why_now",
                "novel_reasoning",
            ):
                text = candidate.get(field_name)
                if isinstance(text, str):
                    records.extend(
                        _semantic_text_records_for_field(
                            field_name=f"blind_predictions.{field_name}",
                            text=text,
                            item_index=item_index,
                        )
                    )
            for field_name in (
                "causal_chain",
                "direct_evidence",
                "inferred_evidence",
                "market_memory_evidence",
                "prior_positive_cases",
                "prior_negative_cases",
                "counterarguments",
                "disconfirming_conditions",
            ):
                records.extend(
                    _semantic_text_records_for_sequence(
                        field_name=f"blind_predictions.{field_name}",
                        values=candidate.get(field_name),
                        item_index=item_index,
                    )
                )
    observed_events = episode.get("observed_events")
    if isinstance(observed_events, list):
        for item_index, event in enumerate(observed_events, start=1):
            if not isinstance(event, dict):
                continue
            for field_name in ("title", "body"):
                text = event.get(field_name)
                if isinstance(text, str):
                    records.extend(
                        _semantic_text_records_for_field(
                            field_name=f"observed_events.{field_name}",
                            text=text,
                            item_index=item_index,
                        )
                    )
    event_ticker_edges = episode.get("event_ticker_edges")
    if isinstance(event_ticker_edges, list):
        for item_index, edge in enumerate(event_ticker_edges, start=1):
            if not isinstance(edge, dict):
                continue
            for field_name in ("relation_explanation", "temporal_validity"):
                text = edge.get(field_name)
                if isinstance(text, str):
                    records.extend(
                        _semantic_text_records_for_field(
                            field_name=f"event_ticker_edges.{field_name}",
                            text=text,
                            item_index=item_index,
                        )
                    )
            for field_name in (
                "fundamental_evidence",
                "narrative_evidence",
                "market_memory_evidence",
            ):
                records.extend(
                    _semantic_text_records_for_sequence(
                        field_name=f"event_ticker_edges.{field_name}",
                        values=edge.get(field_name),
                        item_index=item_index,
                    )
                )
    for field_name in ("lessons", "counterexamples"):
        claims = episode.get(field_name)
        if not isinstance(claims, list):
            continue
        for item_index, claim in enumerate(claims, start=1):
            if not isinstance(claim, dict):
                continue
            for claim_field_name in ("statement", "mechanism", "scope"):
                text = claim.get(claim_field_name)
                if isinstance(text, str):
                    records.extend(
                        _semantic_text_records_for_field(
                            field_name=f"{field_name}.{claim_field_name}",
                            text=text,
                            item_index=item_index,
                        )
                    )
            for claim_field_name in ("conditions", "failure_modes"):
                records.extend(
                    _semantic_text_records_for_sequence(
                        field_name=f"{field_name}.{claim_field_name}",
                        values=claim.get(claim_field_name),
                        item_index=item_index,
                    )
                )
    records.extend(
        _semantic_text_records_for_sequence(
            field_name="misses",
            values=episode.get("misses"),
        )
    )
    return records


def _semantic_text_records_for_sequence(
    *,
    field_name: str,
    values: object,
    item_index: int | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    if not isinstance(values, list):
        return records
    for subitem_index, value in enumerate(values, start=1):
        if not isinstance(value, str):
            continue
        records.extend(
            _semantic_text_records_for_field(
                field_name=f"{field_name}[{subitem_index}]",
                text=value,
                item_index=item_index,
            )
        )
    return records


def _semantic_text_records_for_field(
    *,
    field_name: str,
    text: str,
    item_index: int | None = None,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for sentence_index, segment in enumerate(_semantic_text_segments(text), start=1):
        record: dict[str, object] = {
            "field_name": field_name,
            "sentence_index": sentence_index,
            "text_sha256": segment["text_sha256"],
            "excerpt": segment["excerpt"],
        }
        if item_index is not None:
            record["item_index"] = item_index
        records.append(record)
    return records


def _semantic_text_segments(text: str) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    start = 0
    sentence_endings = {".", "?", "!", "。", "？", "！"}
    for position, character in enumerate(text):
        if character not in sentence_endings and character not in {"\n", "\r"}:
            continue
        next_position = position + 1
        segment = text[start:next_position].strip()
        if segment:
            segments.append(
                {
                    "text_sha256": sha256_text(segment),
                    "excerpt": segment[:240],
                }
            )
        start = next_position
    tail = text[start:].strip()
    if tail:
        segments.append(
            {
                "text_sha256": sha256_text(tail),
                "excerpt": tail[:240],
            }
        )
    return segments


def _semantic_source_segment_indices(semantic: dict[str, Any]) -> set[int]:
    source_segments = semantic.get("source_segments")
    if not isinstance(source_segments, list):
        return set()
    indices: set[int] = set()
    for segment in source_segments:
        if not isinstance(segment, dict):
            continue
        index = segment.get("index")
        if isinstance(index, int) and not isinstance(index, bool):
            indices.add(index)
    return indices


def _semantic_output_text_key(record: dict[str, Any]) -> tuple[str, int | None, int]:
    field_name = record.get("field_name")
    item_index = record.get("item_index")
    sentence_index = record.get("sentence_index")
    return (
        field_name if isinstance(field_name, str) else "",
        item_index if isinstance(item_index, int) and not isinstance(item_index, bool) else None,
        sentence_index
        if isinstance(sentence_index, int) and not isinstance(sentence_index, bool)
        else 0,
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
    _check_manifest_prompt_hash_duplicates(prediction_path, prompt_hashes, findings)
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


def _check_manifest_prompt_hash_duplicates(
    prediction_path: Path,
    prompt_hashes: dict[str, Any],
    findings: list[str],
) -> None:
    fields_by_hash: dict[str, list[str]] = {}
    for field, prompt_hash in prompt_hashes.items():
        if not isinstance(prompt_hash, str) or not prompt_hash:
            continue
        fields_by_hash.setdefault(prompt_hash, []).append(str(field))
    for prompt_hash, fields in sorted(fields_by_hash.items()):
        if len(fields) <= 1:
            continue
        findings.append(
            f"{prediction_path.name}: context manifest prompt_hashes duplicate hash "
            f"{prompt_hash}: {', '.join(sorted(fields))}"
        )


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
    accepted_hashes = _accepted_episode_hashes(root)
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
        source_hashes = _memory_sweep_source_hashes(
            payload.get("episode_shard_source_hashes"),
            episode_ids,
        )
        if source_hashes is None:
            findings.append(
                f"{prediction_path.name}: memory sweep artifact source hashes invalid: "
                f"{artifact_ref}"
            )
        else:
            expected_shard_hash = _memory_sweep_shard_hash(source_hashes)
            if payload.get("episode_shard_sha256") != expected_shard_hash:
                findings.append(
                    f"{prediction_path.name}: memory sweep artifact "
                    f"episode_shard_sha256 mismatch: {artifact_ref}"
                )
            for episode_id, recorded_hash in sorted(source_hashes.items()):
                if accepted_hashes.get(episode_id) != recorded_hash:
                    findings.append(
                        f"{prediction_path.name}: memory sweep artifact source hash "
                        f"mismatch: {artifact_ref}#{episode_id}"
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


def _check_manifest_record_sweep_artifacts(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    raw_artifacts = manifest.get("record_sweep_artifacts")
    if raw_artifacts is None:
        return
    if not isinstance(raw_artifacts, list) or not all(
        isinstance(item, str) and item for item in raw_artifacts
    ):
        findings.append(
            f"{prediction_path.name}: context manifest record_sweep_artifacts is invalid"
        )
        return
    artifact_refs = [str(item) for item in raw_artifacts]
    if not artifact_refs:
        return
    raw_hashes = manifest.get("record_sweep_artifact_hashes")
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
            f"{prediction_path.name}: context manifest record_sweep_artifact_hashes is invalid"
        )
    else:
        hashes = {str(key): str(value) for key, value in raw_hashes.items()}

    if len(artifact_refs) != len(set(artifact_refs)):
        findings.append(f"{prediction_path.name}: context manifest duplicate record sweep artifact")
    missing_hashes = sorted(set(artifact_refs) - set(hashes))
    extra_hashes = sorted(set(hashes) - set(artifact_refs))
    if missing_hashes:
        findings.append(
            f"{prediction_path.name}: context manifest missing record_sweep_artifact_hashes: "
            f"{', '.join(missing_hashes)}"
        )
    if extra_hashes:
        findings.append(
            f"{prediction_path.name}: context manifest unlisted record_sweep_artifact_hashes: "
            f"{', '.join(extra_hashes)}"
        )

    expected_mode = manifest.get("mode")
    expected_trade_date = manifest.get("trade_date")
    expected_cutoff_at = manifest.get("cutoff_at")
    parsed_cutoff_at = _parse_context_cutoff_at(expected_cutoff_at)
    expected_brain_version = manifest.get("brain_version")
    observed_record_ids: list[str] = []
    observed_cache_hits = 0
    records_by_id = {record.record_id: record for record in BrainRecordStore(root).list_records()}
    for artifact_ref in artifact_refs:
        artifact_path = _resolve_manifest_path(root, artifact_ref)
        if artifact_path is None:
            findings.append(
                f"{prediction_path.name}: context manifest record sweep artifact path "
                f"escapes project root: {artifact_ref}"
            )
            continue
        if not artifact_path.exists():
            findings.append(
                f"{prediction_path.name}: context manifest record sweep artifact not found: "
                f"{artifact_ref}"
            )
            continue
        expected_hash = hashes.get(artifact_ref)
        if isinstance(expected_hash, str) and file_sha256(artifact_path) != expected_hash:
            findings.append(
                f"{prediction_path.name}: context manifest record sweep artifact sha256 "
                f"mismatch: {artifact_ref}"
            )
        payload = _read_json_object(artifact_path, findings)
        if payload is None:
            continue
        if payload.get("schema_version") != "nslab.record_memory_sweep_contribution.v1":
            findings.append(
                f"{prediction_path.name}: record sweep artifact schema mismatch: "
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
                    f"{prediction_path.name}: record sweep artifact {field} mismatch: "
                    f"{artifact_ref}"
                )
        record_ids = payload.get("record_ids")
        if not isinstance(record_ids, list) or not all(
            isinstance(record_id, str) for record_id in record_ids
        ):
            findings.append(
                f"{prediction_path.name}: record sweep artifact record_ids invalid: "
                f"{artifact_ref}"
            )
            continue
        observed_record_ids.extend(record_ids)
        if payload.get("record_count") != len(record_ids):
            findings.append(
                f"{prediction_path.name}: record sweep artifact record_count mismatch: "
                f"{artifact_ref}"
            )
        _check_record_sweep_category_fields(
            prediction_path,
            artifact_ref,
            payload,
            record_ids,
            findings,
        )
        source_hashes = _record_sweep_source_hashes(
            payload.get("record_shard_source_hashes"),
            record_ids,
        )
        if source_hashes is None:
            findings.append(
                f"{prediction_path.name}: record sweep artifact source hashes invalid: "
                f"{artifact_ref}"
            )
        else:
            expected_shard_hash = _record_sweep_shard_hash(source_hashes)
            if payload.get("record_shard_sha256") != expected_shard_hash:
                findings.append(
                    f"{prediction_path.name}: record sweep artifact "
                    f"record_shard_sha256 mismatch: {artifact_ref}"
                )
            for record_id, recorded_hash in sorted(source_hashes.items()):
                record = records_by_id.get(record_id)
                actual_hash = record.normalized_payload_sha256 if record is not None else None
                if actual_hash != recorded_hash:
                    findings.append(
                        f"{prediction_path.name}: record sweep artifact source hash "
                        f"mismatch: {artifact_ref}#{record_id}"
                    )
                if (
                    record is not None
                    and parsed_cutoff_at is not None
                    and not is_available_as_of(record.available_from, parsed_cutoff_at)
                ):
                    findings.append(
                        f"{prediction_path.name}: record sweep artifact exposes future "
                        f"record: {artifact_ref}#{record_id}"
                    )
        if payload.get("from_cache") is True:
            observed_cache_hits += 1

    expected_shard_count = manifest.get("record_sweep_shard_count")
    if isinstance(expected_shard_count, int) and expected_shard_count != len(artifact_refs):
        findings.append(f"{prediction_path.name}: context manifest record_sweep_shard_count mismatch")
    expected_cache_hits = manifest.get("record_sweep_cache_hits")
    if isinstance(expected_cache_hits, int) and expected_cache_hits != observed_cache_hits:
        findings.append(f"{prediction_path.name}: context manifest record_sweep_cache_hits mismatch")
    expected_swept_ids = manifest.get("swept_record_ids")
    if isinstance(expected_swept_ids, list) and all(
        isinstance(record_id, str) for record_id in expected_swept_ids
    ):
        if Counter(observed_record_ids) != Counter(expected_swept_ids):
            findings.append(
                f"{prediction_path.name}: context manifest record_sweep swept record ids mismatch"
            )
    else:
        findings.append(f"{prediction_path.name}: context manifest swept_record_ids is invalid")


def _check_record_sweep_category_fields(
    prediction_path: Path,
    artifact_ref: str,
    payload: dict[str, Any],
    record_ids: list[str],
    findings: list[str],
) -> None:
    allowed_record_ids = set(record_ids)
    for field in (
        "positive_analogs",
        "negative_analogs",
        "negative_controls",
        "near_misses",
        "counterexamples",
        "leader_selection_pairs",
        "theme_formation_failures",
        "candidate_generation_errors",
    ):
        value = payload.get(field)
        if not isinstance(value, list):
            findings.append(
                f"{prediction_path.name}: record sweep artifact {field} invalid: "
                f"{artifact_ref}"
            )
            continue
        for item in value:
            if not isinstance(item, dict) or not isinstance(item.get("record_id"), str):
                findings.append(
                    f"{prediction_path.name}: record sweep artifact {field} invalid: "
                    f"{artifact_ref}"
                )
                continue
            record_id = item["record_id"]
            if record_id not in allowed_record_ids:
                findings.append(
                    f"{prediction_path.name}: record sweep artifact {field} references "
                    f"non-shard record: {artifact_ref}#{record_id}"
                )


def _parse_context_cutoff_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_datetime(value)
    except ValueError:
        return None


def _check_manifest_record_count_contract(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    count_fields = {
        "available_record_count": "available_record_ids",
        "training_eligible_available_record_count": (
            "training_eligible_available_record_ids"
        ),
        "swept_record_count": "swept_record_ids",
    }
    for count_field, ids_field in count_fields.items():
        if count_field not in manifest or ids_field not in manifest:
            continue
        expected_count = _non_bool_int(manifest.get(count_field))
        record_ids = _manifest_string_list_or_finding(
            prediction_path,
            manifest,
            ids_field,
            findings,
        )
        if expected_count is None:
            findings.append(
                f"{prediction_path.name}: context manifest {count_field} is invalid"
            )
            continue
        if record_ids is None:
            continue
        if expected_count != len(record_ids):
            findings.append(
                f"{prediction_path.name}: context manifest {count_field} "
                f"does not match {ids_field}"
            )

    if "accepted_record_count" in manifest:
        accepted_record_count = _non_bool_int(manifest.get("accepted_record_count"))
        if accepted_record_count is None:
            findings.append(
                f"{prediction_path.name}: context manifest accepted_record_count is invalid"
            )
        else:
            record_store_count = len(BrainRecordStore(root).list_records())
            if accepted_record_count != record_store_count:
                findings.append(
                    f"{prediction_path.name}: context manifest accepted_record_count "
                    "does not match record store"
                )

    available_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "available_record_ids",
        findings,
    )
    training_eligible_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "training_eligible_available_record_ids",
        findings,
    )
    swept_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "swept_record_ids",
        findings,
    )
    retrieved_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "retrieved_record_ids",
        findings,
    )
    excluded_retrieved_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "excluded_retrieved_record_ids",
        findings,
    )
    counterexample_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "counterexample_record_ids",
        findings,
    )
    semantic_retrieval_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "semantic_retrieval_record_ids",
        findings,
    )
    excluded_semantic_retrieval_record_ids = _optional_manifest_string_list(
        prediction_path,
        manifest,
        "excluded_semantic_retrieval_record_ids",
        findings,
    )
    if available_record_ids is not None and training_eligible_record_ids is not None:
        missing = sorted(set(training_eligible_record_ids) - set(available_record_ids))
        if missing:
            findings.append(
                f"{prediction_path.name}: context manifest "
                "training_eligible_available_record_ids are not a subset of "
                "available_record_ids"
            )
    if available_record_ids is not None and retrieved_record_ids is not None:
        missing = sorted(set(retrieved_record_ids) - set(available_record_ids))
        if missing:
            findings.append(
                f"{prediction_path.name}: context manifest retrieved_record_ids "
                "are not a subset of available_record_ids"
            )
    if (
        retrieved_record_ids is not None
        and excluded_retrieved_record_ids is not None
        and set(retrieved_record_ids) & set(excluded_retrieved_record_ids)
    ):
        findings.append(
            f"{prediction_path.name}: context manifest retrieved_record_ids "
            "overlap excluded_retrieved_record_ids"
        )
    if available_record_ids is not None and counterexample_record_ids is not None:
        missing = sorted(set(counterexample_record_ids) - set(available_record_ids))
        if missing:
            findings.append(
                f"{prediction_path.name}: context manifest counterexample_record_ids "
                "are not a subset of available_record_ids"
            )
    if (
        available_record_ids is not None
        and semantic_retrieval_record_ids is not None
    ):
        missing = sorted(set(semantic_retrieval_record_ids) - set(available_record_ids))
        if missing:
            findings.append(
                f"{prediction_path.name}: context manifest "
                "semantic_retrieval_record_ids are not a subset of "
                "available_record_ids"
            )
    if (
        semantic_retrieval_record_ids is not None
        and excluded_semantic_retrieval_record_ids is not None
        and set(semantic_retrieval_record_ids) & set(excluded_semantic_retrieval_record_ids)
    ):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval_record_ids "
            "overlap excluded_semantic_retrieval_record_ids"
        )
    if (
        manifest.get("mode") in {"exhaustive", "brain"}
        and available_record_ids is not None
        and swept_record_ids is not None
        and Counter(available_record_ids) != Counter(swept_record_ids)
    ):
        findings.append(
            f"{prediction_path.name}: context manifest swept_record_ids "
            "do not match available_record_ids"
        )


def _optional_manifest_string_list(
    prediction_path: Path,
    manifest: dict[str, Any],
    field: str,
    findings: list[str],
) -> list[str] | None:
    if field not in manifest:
        return None
    return _manifest_string_list_or_finding(prediction_path, manifest, field, findings)


def _manifest_string_list_or_finding(
    prediction_path: Path,
    manifest: dict[str, Any],
    field: str,
    findings: list[str],
) -> list[str] | None:
    value = manifest.get(field)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        findings.append(f"{prediction_path.name}: context manifest {field} is invalid")
        return None
    duplicates = _duplicate_strings(value)
    if duplicates:
        findings.append(
            f"{prediction_path.name}: context manifest {field} contains duplicate IDs"
        )
    return value


def _check_manifest_record_id_availability(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    cutoff_at = _parse_context_cutoff_at(manifest.get("cutoff_at"))
    if cutoff_at is None:
        return
    records_by_id = {record.record_id: record for record in BrainRecordStore(root).list_records()}
    for field in (
        "available_record_ids",
        "training_eligible_available_record_ids",
        "swept_record_ids",
        "retrieved_record_ids",
        "counterexample_record_ids",
        "semantic_retrieval_record_ids",
    ):
        if field not in manifest:
            continue
        value = manifest.get(field)
        if not isinstance(value, list) or not all(
            isinstance(record_id, str) for record_id in value
        ):
            findings.append(f"{prediction_path.name}: context manifest {field} is invalid")
            continue
        for record_id in _unique_strings(value):
            record = records_by_id.get(record_id)
            if record is None:
                findings.append(
                    f"{prediction_path.name}: context manifest {field} references "
                    f"unknown record: {record_id}"
                )
                continue
            if not is_available_as_of(record.available_from, cutoff_at):
                findings.append(
                    f"{prediction_path.name}: context manifest {field} exposes future "
                    f"record: {record_id}"
                )


def _memory_sweep_source_hashes(
    value: object,
    episode_ids: list[str],
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        return None
    hashes = {str(key): str(item) for key, item in value.items()}
    if sorted(hashes) != sorted(episode_ids):
        return None
    return hashes


def _memory_sweep_shard_hash(source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"episode_id": episode_id, "source_sha256": source_hash}
                for episode_id, source_hash in sorted(source_hashes.items())
            ]
        )
    )


def _record_sweep_source_hashes(
    value: object,
    record_ids: list[str],
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        return None
    hashes = {str(key): str(item) for key, item in value.items()}
    if sorted(hashes) != sorted(record_ids):
        return None
    return hashes


def _record_sweep_shard_hash(source_hashes: dict[str, str]) -> str:
    return sha256_text(
        canonical_json(
            [
                {"record_id": record_id, "source_sha256": source_hash}
                for record_id, source_hash in sorted(source_hashes.items())
            ]
        )
    )


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
    _check_open_world_first_analysis_artifact(root, prediction_path, manifest, findings)
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


def _check_retrieval_miss_open_world_outputs(
    prediction_path: Path,
    prediction: dict[str, Any],
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    if not _manifest_has_semantic_retrieval_miss(manifest):
        return
    blind_analysis = prediction.get("blind_analysis")
    mechanisms = (
        _string_list(blind_analysis.get("open_world_mechanisms"))
        if isinstance(blind_analysis, dict)
        else []
    )
    if not mechanisms:
        findings.append(
            f"{prediction_path.name}: retrieval miss missing open-world mechanisms"
        )
    candidates = prediction.get("candidates")
    if not isinstance(candidates, list) or not any(
        isinstance(candidate, dict) for candidate in candidates
    ):
        findings.append(f"{prediction_path.name}: retrieval miss produced no candidates")
    sectors = prediction.get("dominant_sectors")
    if not isinstance(sectors, list) or not any(isinstance(sector, dict) for sector in sectors):
        findings.append(
            f"{prediction_path.name}: retrieval miss produced no dominant sectors"
        )
    candidate_expansion_count = _non_bool_int(manifest.get("candidate_expansion_count"))
    if candidate_expansion_count is not None and candidate_expansion_count < 1:
        findings.append(
            f"{prediction_path.name}: retrieval miss produced no candidate expansion"
        )
    summary = manifest.get("candidate_expansion_summary")
    if not isinstance(summary, dict):
        return
    candidate_name_count = _non_bool_int(summary.get("candidate_name_count"))
    if candidate_name_count is not None and candidate_name_count < 1:
        findings.append(
            f"{prediction_path.name}: retrieval miss candidate expansion has no candidates"
        )
    web_discovery_count = _non_bool_int(
        summary.get("requires_web_company_discovery_count")
    )
    if web_discovery_count is not None and web_discovery_count < 1:
        findings.append(
            f"{prediction_path.name}: retrieval miss missing web company discovery plan"
        )


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


def _check_open_world_first_analysis_artifact(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    artifact_ref = manifest.get("open_world_first_analysis_artifact")
    expected_hash = manifest.get("open_world_first_analysis_sha256")
    if artifact_ref is None and expected_hash is None:
        return
    artifact_path = _resolve_required_manifest_artifact(
        root,
        prediction_path,
        artifact_ref,
        label="open_world_first_analysis_artifact",
        findings=findings,
    )
    if artifact_path is None:
        return
    text = artifact_path.read_text(encoding="utf-8", errors="replace")
    if not isinstance(expected_hash, str) or not expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest missing "
            "open_world_first_analysis_sha256"
        )
    elif sha256_text(text) != expected_hash:
        findings.append(
            f"{prediction_path.name}: context manifest "
            "open_world_first_analysis_sha256 mismatch"
        )
    payload = _read_json_object(artifact_path, findings)
    if payload is None:
        return
    if payload.get("schema_version") != "nslab.open_world_first_analysis.v1":
        findings.append(
            f"{prediction_path.name}: context manifest open_world_first_analysis "
            "schema_version mismatch"
        )
    run_id = manifest.get("run_id")
    if isinstance(run_id, str) and payload.get("run_id") != run_id:
        findings.append(
            f"{prediction_path.name}: context manifest open_world_first_analysis "
            "run_id mismatch"
        )
    prompt_hash = _manifest_prompt_hash(manifest, "open_world_first_analysis")
    if isinstance(prompt_hash, str) and payload.get("prompt_sha256") != prompt_hash:
        findings.append(
            f"{prediction_path.name}: context manifest open_world_first_analysis "
            "prompt_hash mismatch"
        )
    _check_open_world_first_analysis_summary(
        prediction_path,
        manifest,
        payload,
        findings,
    )


def _check_open_world_first_analysis_summary(
    prediction_path: Path,
    manifest: dict[str, Any],
    payload: dict[str, Any],
    findings: list[str],
) -> None:
    required_fields = (
        "event_clusters",
        "direct_company_events",
        "policy_industry_events",
        "mechanisms",
        "beneficiary_transmission_paths",
        "narrative_conversion_points",
        "direct_candidates",
        "potential_sectors",
        "beneficiary_investigation_questions",
        "uncertainties",
    )
    for field in required_fields:
        if not _string_list(payload.get(field)):
            findings.append(
                f"{prediction_path.name}: context manifest open_world_first_analysis "
                f"{field} empty"
            )
    summary = manifest.get("open_world_first_analysis_summary")
    if not isinstance(summary, dict):
        findings.append(
            f"{prediction_path.name}: context manifest "
            "open_world_first_analysis_summary invalid"
        )
        return
    expected_counts = {
        "event_cluster_count": len(_string_list(payload.get("event_clusters"))),
        "direct_company_event_count": len(
            _string_list(payload.get("direct_company_events"))
        ),
        "policy_industry_event_count": len(
            _string_list(payload.get("policy_industry_events"))
        ),
        "mechanism_count": len(_string_list(payload.get("mechanisms"))),
        "transmission_path_count": len(
            _string_list(payload.get("beneficiary_transmission_paths"))
        ),
        "narrative_conversion_point_count": len(
            _string_list(payload.get("narrative_conversion_points"))
        ),
        "direct_candidate_count": len(_string_list(payload.get("direct_candidates"))),
        "potential_sector_count": len(_string_list(payload.get("potential_sectors"))),
        "investigation_question_count": len(
            _string_list(payload.get("beneficiary_investigation_questions"))
        ),
        "uncertainty_count": len(_string_list(payload.get("uncertainties"))),
    }
    for field, expected in expected_counts.items():
        _check_summary_int(
            prediction_path,
            summary,
            field,
            expected,
            label="open_world_first_analysis",
            findings=findings,
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
    included_record_ids = _unique_strings(
        record_id
        for row in rows
        for record_id in _string_list(row.get("included_record_ids"))
    )
    excluded_record_ids = _unique_strings(
        record_id
        for row in rows
        for record_id in _string_list(row.get("excluded_record_ids"))
    )
    record_contract_required = _semantic_retrieval_record_contract_required(
        manifest,
        summary,
        rows,
    )
    if (
        record_contract_required
        and included_record_ids
        != _string_list(manifest.get("semantic_retrieval_record_ids"))
    ):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "included_record_ids mismatch"
        )
    if (
        record_contract_required
        and excluded_record_ids
        != _string_list(manifest.get("excluded_semantic_retrieval_record_ids"))
    ):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "excluded_record_ids mismatch"
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
    if record_contract_required:
        _check_summary_int(
            prediction_path,
            summary,
            "included_record_count",
            len(included_record_ids),
            label="semantic_retrieval",
            findings=findings,
        )
        _check_summary_int(
            prediction_path,
            summary,
            "excluded_record_count",
            len(excluded_record_ids),
            label="semantic_retrieval",
            findings=findings,
        )
    if summary.get("retrieval_zero_is_valid") is not True:
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "zero_policy missing"
        )
    if (
        record_contract_required
        and summary.get("record_retrieval_zero_is_valid") is not True
    ):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "record_zero_policy missing"
        )
    expected_categories = _semantic_retrieval_required_categories(manifest)
    if expected_categories and set(category_counts) != set(expected_categories):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval "
            "category coverage mismatch"
        )
    for index, row in enumerate(rows, start=1):
        _check_semantic_retrieval_row(prediction_path, index, row, findings)


def _manifest_has_semantic_retrieval_miss(manifest: dict[str, Any]) -> bool:
    summary = manifest.get("semantic_retrieval_summary")
    return (
        isinstance(summary, dict)
        and summary.get("retrieval_zero_is_valid") is True
        and _string_list(manifest.get("semantic_retrieval_episode_ids")) == []
    )


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
    included_record_ids = _string_list(row.get("included_record_ids"))
    excluded_record_ids = _string_list(row.get("excluded_record_ids"))
    result_count = _non_bool_int(row.get("result_count"))
    excluded_count = _non_bool_int(row.get("excluded_count"))
    record_result_count = _non_bool_int(row.get("record_result_count"))
    excluded_record_count = _non_bool_int(row.get("excluded_record_count"))
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
    if record_result_count is not None and record_result_count != len(included_record_ids):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval:{index} "
            "record_result_count mismatch"
        )
    if excluded_record_count is not None and excluded_record_count != len(excluded_record_ids):
        findings.append(
            f"{prediction_path.name}: context manifest semantic_retrieval:{index} "
            "excluded_record_count mismatch"
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
    if _manifest_has_semantic_retrieval_miss(manifest):
        _check_candidate_expansion_retrieval_miss_rows(
            prediction_path,
            findings_rows,
            findings,
        )


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


def _check_candidate_expansion_retrieval_miss_rows(
    prediction_path: Path,
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    for index, row in enumerate(rows, start=1):
        for field in ("candidate_names", "sector_hypotheses", "investigation_questions"):
            if not _string_list(row.get(field)):
                findings.append(
                    f"{prediction_path.name}: context manifest "
                    f"candidate_expansion:{index} retrieval miss {field} empty"
                )
        path = row.get("path")
        if path in {"SINGLE_EVENT", "THEME_FORMATION", "BENEFICIARY_DISCOVERY"} and (
            row.get("requires_web_company_discovery") is not True
        ):
            findings.append(
                f"{prediction_path.name}: context manifest candidate_expansion:{index} "
                "retrieval miss web discovery missing"
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
    _check_candidate_web_check_summary_int(
        prediction_path,
        summary,
        "source_count",
        len(rows),
        findings=findings,
    )
    _check_candidate_web_check_summary_int(
        prediction_path,
        summary,
        "excluded_source_count",
        len(excluded_rows),
        findings=findings,
    )
    subject_rows = [*rows, *excluded_rows]
    subject_keys = _candidate_web_check_subject_keys(subject_rows)
    final_candidate_keys = _candidate_web_check_subject_keys(
        row
        for row in subject_rows
        if row.get("candidate_subject_type") == "final_candidate"
    )
    expansion_subject_keys = _candidate_web_check_subject_keys(
        row
        for row in subject_rows
        if row.get("candidate_subject_type") == "candidate_expansion"
    )
    _check_candidate_web_check_summary_int(
        prediction_path,
        summary,
        "subject_count",
        len(subject_keys),
        findings=findings,
    )
    _check_candidate_web_check_summary_int(
        prediction_path,
        summary,
        "final_candidate_subject_count",
        len(final_candidate_keys),
        findings=findings,
    )
    _check_candidate_web_check_summary_int(
        prediction_path,
        summary,
        "candidate_expansion_subject_count",
        len(expansion_subject_keys),
        findings=findings,
    )
    expansion_paths = _candidate_web_check_expansion_paths(subject_rows)
    if _string_list(summary.get("expansion_paths")) != expansion_paths:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_web_check "
            "expansion_paths mismatch"
        )
    expected_focus = _string_list(summary.get("verification_focus"))
    if expected_focus and any(
        _string_list(row.get("verification_focus")) != expected_focus for row in rows
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_web_check "
            "verification_focus mismatch"
        )


def _check_candidate_web_check_summary_int(
    prediction_path: Path,
    summary: dict[str, Any],
    field: str,
    expected: int,
    *,
    findings: list[str],
) -> None:
    if _non_bool_int(summary.get(field)) != expected:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_web_check "
            f"{field} mismatch"
        )


def _candidate_web_check_subject_keys(
    rows: Iterable[dict[str, Any]],
) -> set[tuple[str, int, str, str, str, str | None]]:
    return {_candidate_web_check_subject_key(row) for row in rows}


def _candidate_web_check_subject_key(
    row: dict[str, Any],
) -> tuple[str, int, str, str, str, str | None]:
    rank = row.get("candidate_rank")
    expansion_path = row.get("candidate_expansion_path")
    return (
        str(row.get("candidate_subject_type") or ""),
        rank if isinstance(rank, int) and not isinstance(rank, bool) else 0,
        str(row.get("candidate_ticker") or ""),
        str(row.get("candidate_company_name") or ""),
        str(row.get("candidate_path_type") or ""),
        str(expansion_path) if expansion_path is not None else None,
    )


def _candidate_web_check_expansion_paths(rows: Iterable[dict[str, Any]]) -> list[str]:
    return sorted(
        {
            str(row["candidate_expansion_path"])
            for row in rows
            if row.get("candidate_expansion_path") is not None
        }
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
    _check_candidate_verification_market_snapshots(
        prediction_path,
        summary,
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


def _check_candidate_verification_market_snapshots(
    prediction_path: Path,
    summary: dict[str, Any],
    candidate_findings: list[dict[str, Any]],
    findings: list[str],
) -> None:
    if (
        "d_minus_one_snapshot_count" not in summary
        and "d_minus_one_snapshot_unavailable_count" not in summary
    ):
        return
    for index, finding in enumerate(candidate_findings, start=1):
        snapshot = finding.get("blind_safe_market_snapshot")
        if not isinstance(snapshot, dict):
            findings.append(
                f"{prediction_path.name}: context manifest "
                f"candidate_verification:{index} blind_safe_market_snapshot invalid"
            )
            continue
        if snapshot.get("status") not in {"snapshot", "unavailable"}:
            findings.append(
                f"{prediction_path.name}: context manifest "
                f"candidate_verification:{index} blind_safe_market_snapshot status invalid"
            )
        if snapshot.get("status") == "snapshot" and not isinstance(
            snapshot.get("snapshot"), dict
        ):
            findings.append(
                f"{prediction_path.name}: context manifest "
                f"candidate_verification:{index} blind_safe_market_snapshot payload invalid"
            )
        if snapshot.get("status") == "unavailable" and not isinstance(
            snapshot.get("reason"), str
        ):
            findings.append(
                f"{prediction_path.name}: context manifest "
                f"candidate_verification:{index} blind_safe_market_snapshot reason missing"
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

    without_sources_count = sum(
        1
        for finding in candidate_findings
        if not _string_list(finding.get("accepted_source_ids"))
    )
    if (
        _non_bool_int(summary.get("subjects_without_cutoff_safe_sources"))
        != without_sources_count
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "subjects_without_cutoff_safe_sources mismatch"
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

    snapshot_count = sum(
        1
        for finding in candidate_findings
        if isinstance(finding.get("blind_safe_market_snapshot"), dict)
        and finding["blind_safe_market_snapshot"].get("status") == "snapshot"
    )
    unavailable_count = sum(
        1
        for finding in candidate_findings
        if isinstance(finding.get("blind_safe_market_snapshot"), dict)
        and finding["blind_safe_market_snapshot"].get("status") != "snapshot"
    )
    expected_snapshot_count = _non_bool_int(summary.get("d_minus_one_snapshot_count"))
    if expected_snapshot_count is not None and expected_snapshot_count != snapshot_count:
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "d_minus_one_snapshot_count mismatch"
        )
    expected_unavailable_count = _non_bool_int(
        summary.get("d_minus_one_snapshot_unavailable_count")
    )
    if (
        expected_unavailable_count is not None
        and expected_unavailable_count != unavailable_count
    ):
        findings.append(
            f"{prediction_path.name}: context manifest candidate_verification "
            "d_minus_one_snapshot_unavailable_count mismatch"
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


def _duplicate_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


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
    if section_status["empty"]:
        empty = ", ".join(section_status["empty"])
        findings.append(
            f"{prediction_path.name}: context manifest report_artifact empty "
            f"required sections: {empty}"
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
    _check_final_synthesis_manifest_record_ids(
        prediction_path,
        manifest,
        context_payload,
        findings,
    )
    _check_final_synthesis_record_id_availability(
        root,
        prediction_path,
        manifest,
        context_payload,
        findings,
    )
    _check_final_synthesis_embedded_artifacts(
        root,
        prediction_path,
        manifest,
        context_payload,
        findings,
    )


def _check_final_synthesis_manifest_record_ids(
    prediction_path: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    findings: list[str],
) -> None:
    for field in (
        "retrieved_record_ids",
        "excluded_retrieved_record_ids",
        "semantic_retrieval_record_ids",
        "excluded_semantic_retrieval_record_ids",
        "counterexample_record_ids",
    ):
        if field not in manifest and field not in context_payload:
            continue
        manifest_ids = _final_synthesis_string_list(
            manifest.get(field),
            prediction_path=prediction_path,
            source="context manifest",
            field=field,
            findings=findings,
        )
        payload_ids = _final_synthesis_string_list(
            context_payload.get(field),
            prediction_path=prediction_path,
            source="final_synthesis_context",
            field=field,
            findings=findings,
        )
        if manifest_ids is None or payload_ids is None:
            continue
        if manifest_ids != payload_ids:
            findings.append(
                f"{prediction_path.name}: final_synthesis_context {field} "
                "does not match context manifest"
            )


def _final_synthesis_string_list(
    value: object,
    *,
    prediction_path: Path,
    source: str,
    field: str,
    findings: list[str],
) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        findings.append(f"{prediction_path.name}: {source} {field} is invalid")
        return None
    return value


def _check_final_synthesis_record_id_availability(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    context_payload: dict[str, Any],
    findings: list[str],
) -> None:
    cutoff_at = _parse_context_cutoff_at(manifest.get("cutoff_at"))
    if cutoff_at is None:
        return
    records_by_id = {record.record_id: record for record in BrainRecordStore(root).list_records()}
    field_record_ids: dict[str, list[str]] = {}
    for field in (
        "retrieved_record_ids",
        "semantic_retrieval_record_ids",
        "counterexample_record_ids",
        "positive_record_ids",
        "negative_record_ids",
    ):
        if field not in context_payload:
            continue
        value = context_payload.get(field)
        if not isinstance(value, list) or not all(
            isinstance(record_id, str) for record_id in value
        ):
            findings.append(
                f"{prediction_path.name}: final_synthesis_context {field} is invalid"
            )
            continue
        field_record_ids[field] = _unique_strings(value)
    _collect_final_synthesis_record_object_ids(
        context_payload,
        field_record_ids=field_record_ids,
        findings=findings,
        prediction_path=prediction_path,
    )
    for field, record_ids in field_record_ids.items():
        for record_id in record_ids:
            record = records_by_id.get(record_id)
            if record is None:
                findings.append(
                    f"{prediction_path.name}: final_synthesis_context {field} "
                    f"references unknown record: {record_id}"
                )
                continue
            if not is_available_as_of(record.available_from, cutoff_at):
                findings.append(
                    f"{prediction_path.name}: final_synthesis_context {field} "
                    f"exposes future record: {record_id}"
                )


def _collect_final_synthesis_record_object_ids(
    context_payload: dict[str, Any],
    *,
    field_record_ids: dict[str, list[str]],
    findings: list[str],
    prediction_path: Path,
) -> None:
    for field in ("retrieved_records", "counterexample_records"):
        value = context_payload.get(field)
        if value is None:
            continue
        if not isinstance(value, list):
            findings.append(
                f"{prediction_path.name}: final_synthesis_context {field} is invalid"
            )
            continue
        ids: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                findings.append(
                    f"{prediction_path.name}: final_synthesis_context {field} is invalid"
                )
                ids = []
                break
            record_id = item.get("record_id")
            if isinstance(record_id, str) and record_id:
                ids.append(record_id)
        if ids:
            field_record_ids[field] = _unique_strings(ids)

    contributions = context_payload.get("record_level_shard_contributions")
    if contributions is None:
        return
    if not isinstance(contributions, list):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "record_level_shard_contributions is invalid"
        )
        return
    ids = []
    for item in contributions:
        if not isinstance(item, dict):
            findings.append(
                f"{prediction_path.name}: final_synthesis_context "
                "record_level_shard_contributions is invalid"
            )
            ids = []
            break
        payload = item.get("payload")
        if not isinstance(payload, dict):
            continue
        record_ids = payload.get("record_ids")
        if isinstance(record_ids, list):
            ids.extend(record_id for record_id in record_ids if isinstance(record_id, str))
    if ids:
        field_record_ids["record_level_shard_contributions"] = _unique_strings(ids)


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

    open_world_first_analysis = _read_optional_manifest_object(
        root,
        manifest.get("open_world_first_analysis_artifact"),
    )
    if (
        open_world_first_analysis is not None
        and context_payload.get("open_world_first_analysis")
        != open_world_first_analysis
    ):
        findings.append(
            f"{prediction_path.name}: final_synthesis_context "
            "open_world_first_analysis mismatch"
        )

    semantic_retrieval_rows = _read_optional_manifest_jsonl_rows(
        root,
        manifest.get("semantic_retrieval_artifact"),
    )
    if semantic_retrieval_rows is not None:
        _check_final_synthesis_semantic_retrieval_context(
            root,
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


def _semantic_retrieval_record_context(
    root: Path,
    manifest: dict[str, Any],
) -> list[dict[str, Any]]:
    store = BrainRecordStore(root)
    records: list[dict[str, Any]] = []
    for record_id in _string_list(manifest.get("semantic_retrieval_record_ids")):
        try:
            record = store.get_record(record_id)
        except FileNotFoundError:
            records.append({"record_id": record_id, "missing": True})
            continue
        records.append(record.model_dump(mode="json"))
    return records


def _semantic_retrieval_record_contract_required(
    manifest: dict[str, Any],
    summary: object,
    rows: list[dict[str, Any]],
) -> bool:
    if (
        "semantic_retrieval_record_ids" in manifest
        or "excluded_semantic_retrieval_record_ids" in manifest
    ):
        return True
    if isinstance(summary, dict) and any(
        key in summary
        for key in (
            "included_record_count",
            "excluded_record_count",
            "record_retrieval_zero_is_valid",
        )
    ):
        return True
    return any(
        "included_record_ids" in row
        or "excluded_record_ids" in row
        or "record_result_count" in row
        or "excluded_record_count" in row
        for row in rows
    )


def _check_final_synthesis_semantic_retrieval_context(
    root: Path,
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
    if _semantic_retrieval_record_contract_required(
        manifest,
        manifest.get("semantic_retrieval_summary"),
        semantic_retrieval_rows,
    ):
        expected_fields.update(
            {
                "included_episode_ids": manifest.get("semantic_retrieval_episode_ids"),
                "included_record_ids": manifest.get("semantic_retrieval_record_ids"),
                "records": _semantic_retrieval_record_context(root, manifest),
                "excluded_record_ids": manifest.get(
                    "excluded_semantic_retrieval_record_ids"
                ),
            }
        )
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
        "open_world_first_analysis": "open_world_first_analysis",
        "news_novelty_review": "news_novelty_review",
        "semantic_retrieval_plan": "semantic_retrieval_plan",
        "candidate_expansion": "candidate_expansion",
        "blind_analysis": "daily_blind_analysis",
        "red_team_candidate_review": "red_team_candidate_review",
        "final_synthesis": "final_synthesis",
    }
    token_key_by_hash_key = {
        "open_world_first_analysis": "open_world_first_analysis_prompt",
        "news_novelty_review": "news_novelty_review_prompt",
        "semantic_retrieval_plan": "semantic_retrieval_plan_prompt",
        "candidate_expansion": "candidate_expansion_prompt",
        "blind_analysis": "blind_analysis_prompt",
        "red_team_candidate_review": "red_team_prompt",
        "final_synthesis": "final_synthesis_prompt",
    }
    traces_by_purpose = _trace_metadata_by_purpose(root, findings)
    requires_current_traces = manifest.get("schema_version") == "nslab.context_manifest.v1"
    for hash_key, purpose in purpose_by_hash_key.items():
        manifest_hash = prompt_hashes.get(hash_key)
        if not manifest_hash:
            continue
        if purpose not in traces_by_purpose:
            if requires_current_traces:
                findings.append(
                    f"{prediction_path.name}: prompt hash has no matching trace for {purpose}"
                )
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
        _check_trace_prompt_token_counts_match_manifest(
            prediction_path,
            manifest,
            purpose,
            token_key_by_hash_key[hash_key],
            matching_trace_records,
            findings,
        )
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


def _check_trace_prompt_token_counts_match_manifest(
    prediction_path: Path,
    manifest: dict[str, Any],
    purpose: str,
    token_count_key: str,
    trace_records: list[dict[str, Any]],
    findings: list[str],
) -> None:
    token_counts = manifest.get("token_counts")
    if not isinstance(token_counts, dict):
        return
    expected_prompt_tokens = token_counts.get(token_count_key)
    if (
        not isinstance(expected_prompt_tokens, int)
        or isinstance(expected_prompt_tokens, bool)
        or expected_prompt_tokens < 0
    ):
        findings.append(
            f"{prediction_path.name}: context manifest missing {token_count_key} "
            f"token count for {purpose}"
        )
        return
    mismatched_records: list[dict[str, Any]] = []
    for trace_record in trace_records:
        payload = trace_record["payload"]
        mismatch = _trace_prompt_token_count_mismatch(payload, expected_prompt_tokens)
        if not mismatch:
            return
        mismatched_records.append(trace_record)
    if mismatched_records:
        trace_names = ", ".join(
            trace_record["path"].name for trace_record in mismatched_records
        )
        findings.append(
            f"{prediction_path.name}: trace prompt token count mismatch for "
            f"{purpose}: {trace_names}"
        )


def _trace_prompt_token_count_mismatch(
    payload: dict[str, Any],
    expected_prompt_tokens: int,
) -> bool:
    token_usage = payload.get("token_usage")
    trace_input = payload.get("input")
    observed_prompt_tokens = (
        token_usage.get("prompt_tokens_estimate")
        if isinstance(token_usage, dict)
        else None
    )
    prompt_chars = trace_input.get("prompt_chars") if isinstance(trace_input, dict) else None
    if not isinstance(observed_prompt_tokens, int) or isinstance(
        observed_prompt_tokens, bool
    ):
        return True
    if observed_prompt_tokens != expected_prompt_tokens:
        return True
    prompt_chars_token_estimate = _estimate_prompt_tokens_from_chars(prompt_chars)
    return (
        prompt_chars_token_estimate is None
        or observed_prompt_tokens != prompt_chars_token_estimate
    )


def _estimate_prompt_tokens_from_chars(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return max(1, value // 4) if value else 0


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
        if payload.get("operation") == "embed":
            _check_trace_checkpoint(root, path, payload, findings)
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
    if operation == "embed":
        findings.extend(
            f"{path.name}: {error}"
            for error in _embedding_trace_output_findings(output)
        )
    if not isinstance(payload.get("tool_calls"), list):
        findings.append(f"{path.name}: trace tool_calls is not a list")
    retries = payload.get("retries")
    if not isinstance(retries, int) or isinstance(retries, bool):
        findings.append(f"{path.name}: trace retries is not an integer")
        retries = None
    for error in _retry_error_history_findings(
        label=f"{path.name}: trace retry_errors",
        value=payload.get("retry_errors"),
        retries=retries,
    ):
        findings.append(error)
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
    checkpoint_retries = checkpoint.get("retries")
    normalized_checkpoint_retries = (
        checkpoint_retries
        if isinstance(checkpoint_retries, int) and not isinstance(checkpoint_retries, bool)
        else None
    )
    for error in _retry_error_history_findings(
        label=f"{trace_path.name}: trace checkpoint retry_errors",
        value=checkpoint.get("retry_errors"),
        retries=normalized_checkpoint_retries,
    ):
        findings.append(error)
    trace_retries = trace_payload.get("retries")
    trace_retry_count = (
        trace_retries
        if isinstance(trace_retries, int) and not isinstance(trace_retries, bool)
        else None
    )
    if (
        (
            "retry_errors" in checkpoint
            or "retry_errors" in trace_payload
            or (trace_retry_count is not None and trace_retry_count > 0)
        )
        and checkpoint.get("retry_errors") != trace_payload.get("retry_errors", [])
    ):
        findings.append(f"{trace_path.name}: trace checkpoint retry_errors mismatch")
    if "token_usage" in checkpoint:
        checkpoint_token_usage = checkpoint.get("token_usage")
        if not isinstance(checkpoint_token_usage, dict):
            findings.append(f"{trace_path.name}: trace checkpoint token_usage is not an object")
        elif checkpoint_token_usage != trace_payload.get("token_usage"):
            findings.append(f"{trace_path.name}: trace checkpoint token_usage mismatch")
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


def _embedding_trace_output_findings(output: object) -> list[str]:
    if not isinstance(output, dict):
        return ["trace embed output summary is not an object"]
    findings: list[str] = []
    vector_count = output.get("vector_count")
    dimensions = output.get("dimensions")
    vectors_sha256 = output.get("vectors_sha256")
    if not isinstance(vector_count, int) or isinstance(vector_count, bool) or vector_count < 0:
        findings.append("trace embed output vector_count invalid")
    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions < 0:
        findings.append("trace embed output dimensions invalid")
    if not isinstance(vectors_sha256, str) or not vectors_sha256:
        findings.append("trace embed output vectors_sha256 missing")
    return findings


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


def _retry_error_history_findings(
    *,
    label: str,
    value: object,
    retries: int | None,
) -> list[str]:
    findings: list[str] = []
    if value is None:
        if retries and retries > 0:
            findings.append(f"{label} missing")
        return findings
    if not isinstance(value, list):
        return [f"{label} is not a list"]
    if retries is not None and retries > 0 and len(value) != retries:
        findings.append(f"{label} count mismatch")
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            findings.append(f"{label} item {index} is not an object")
            continue
        if not isinstance(item.get("type"), str) or not item.get("type"):
            findings.append(f"{label} item {index} missing type")
        if not isinstance(item.get("message"), str):
            findings.append(f"{label} item {index} missing message")
    return findings


def _check_session_pack_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for manifest_path in sorted((root / "session_packs").glob("*/manifest.json")):
        manifest = _read_json_object(manifest_path, findings)
        if manifest is None:
            continue
        checked += 1
        _check_session_pack_manifest(root, manifest_path, manifest, findings)
    return checked


def _check_session_pack_manifest(
    root: Path,
    manifest_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    label = _display_path(root, manifest_path)
    if manifest.get("schema_version") != "nslab.session_pack_manifest.v1":
        findings.append(f"{label}: session pack schema_version invalid")
    for field in ("trade_date", "cutoff_at", "as_of", "mode", "brain_version"):
        if not isinstance(manifest.get(field), str) or not manifest.get(field):
            findings.append(f"{label}: session pack {field} missing")
    if not isinstance(manifest.get("blocked"), bool):
        findings.append(f"{label}: session pack blocked invalid")

    _check_session_pack_context_hashes(
        root,
        label,
        manifest,
        files_field="brain_files",
        hashes_field="brain_file_hashes",
        findings=findings,
    )
    _check_session_pack_context_hashes(
        root,
        label,
        manifest,
        files_field="shard_brain_files",
        hashes_field="shard_brain_file_hashes",
        findings=findings,
    )
    shard_brain_files = _session_pack_string_list_field(
        label,
        manifest,
        "shard_brain_files",
        findings,
    )
    shard_brain_count = _non_bool_int(manifest.get("shard_brain_count"))
    if shard_brain_count is None:
        findings.append(f"{label}: session pack shard_brain_count invalid")
    elif shard_brain_files is not None and shard_brain_count != len(shard_brain_files):
        findings.append(f"{label}: session pack shard_brain_count mismatch")
    observed_token_counts = _check_session_pack_files(
        label,
        manifest_path,
        manifest,
        findings,
    )
    observed_total = _check_session_pack_token_counts(
        label,
        manifest,
        observed_token_counts,
        findings,
    )
    _check_session_pack_omission_report(root, label, manifest_path, manifest, findings)
    _check_session_pack_blocking_contract(
        label,
        manifest,
        observed_total=observed_total,
        findings=findings,
    )


def _check_session_pack_context_hashes(
    root: Path,
    label: str,
    manifest: dict[str, Any],
    *,
    files_field: str,
    hashes_field: str,
    findings: list[str],
) -> None:
    file_refs = _session_pack_string_list_field(label, manifest, files_field, findings)
    hashes = _session_pack_hash_dict_field(label, manifest, hashes_field, findings)
    if file_refs is None or hashes is None:
        return
    missing_hashes = sorted(set(file_refs) - set(hashes))
    extra_hashes = sorted(set(hashes) - set(file_refs))
    for file_ref in missing_hashes:
        findings.append(
            f"{label}: session pack {hashes_field} missing: {file_ref}"
        )
    for file_ref in extra_hashes:
        findings.append(
            f"{label}: session pack {hashes_field} unlisted: {file_ref}"
        )
    for file_ref in file_refs:
        expected_hash = hashes.get(file_ref)
        if not isinstance(expected_hash, str):
            continue
        path = _resolve_project_path(root, file_ref)
        if path is None:
            findings.append(
                f"{label}: session pack {files_field} path escapes project root: "
                f"{file_ref}"
            )
            continue
        if not path.is_file():
            findings.append(
                f"{label}: session pack {files_field} file not found: {file_ref}"
            )
            continue
        if file_sha256(path) != expected_hash:
            findings.append(
                f"{label}: session pack {hashes_field} mismatch: {file_ref}"
            )


def _check_session_pack_files(
    label: str,
    manifest_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> dict[str, int]:
    pack_files = _session_pack_string_list_field(label, manifest, "pack_files", findings)
    if pack_files is not None and pack_files != list(SESSION_PACK_FILES):
        findings.append(f"{label}: session pack pack_files mismatch")
    pack_file_count = _non_bool_int(manifest.get("pack_file_count"))
    if pack_file_count != len(SESSION_PACK_FILES):
        findings.append(f"{label}: session pack pack_file_count mismatch")

    pack_hashes = _session_pack_hash_dict_field(
        label,
        manifest,
        "pack_file_hashes",
        findings,
    )
    if pack_hashes is None:
        return {}
    observed_hashes: dict[str, str] = {}
    observed_token_counts: dict[str, int] = {}
    for file_name in SESSION_PACK_FILES:
        expected_hash = pack_hashes.get(file_name)
        if not isinstance(expected_hash, str):
            findings.append(
                f"{label}: session pack pack_file_hashes missing: {file_name}"
            )
            continue
        path = manifest_path.parent / file_name
        if not path.is_file():
            findings.append(f"{label}: session pack file missing: {file_name}")
            continue
        observed_hash = file_sha256(path)
        observed_hashes[file_name] = observed_hash
        observed_token_counts[file_name] = _estimate_session_pack_tokens(
            path.read_text(encoding="utf-8")
        )
        if observed_hash != expected_hash:
            findings.append(
                f"{label}: session pack pack_file_hashes mismatch: {file_name}"
            )
    extra_hashes = sorted(str(key) for key in pack_hashes if key not in SESSION_PACK_FILES)
    if extra_hashes:
        findings.append(
            f"{label}: session pack unlisted pack_file_hashes: "
            f"{', '.join(extra_hashes)}"
        )
    expected_pack_sha = manifest.get("pack_sha256")
    if not isinstance(expected_pack_sha, str) or not expected_pack_sha:
        findings.append(f"{label}: session pack pack_sha256 missing")
    elif set(observed_hashes) == set(SESSION_PACK_FILES):
        observed_pack_sha = sha256_text(
            "\n".join(observed_hashes[file_name] for file_name in SESSION_PACK_FILES)
        )
        if observed_pack_sha != expected_pack_sha:
            findings.append(f"{label}: session pack pack_sha256 mismatch")
    return observed_token_counts


def _check_session_pack_token_counts(
    label: str,
    manifest: dict[str, Any],
    observed_token_counts: dict[str, int],
    findings: list[str],
) -> int | None:
    token_counts = manifest.get("token_counts")
    if not isinstance(token_counts, dict):
        findings.append(f"{label}: session pack token_counts invalid")
        return None
    expected_total = 0
    valid_expected_counts = True
    for file_name in SESSION_PACK_FILES:
        expected_count = token_counts.get(file_name)
        if not isinstance(expected_count, int) or isinstance(expected_count, bool):
            findings.append(f"{label}: session pack token_counts missing: {file_name}")
            valid_expected_counts = False
            continue
        expected_total += expected_count
        observed_count = observed_token_counts.get(file_name)
        if observed_count is not None and observed_count != expected_count:
            findings.append(
                f"{label}: session pack token_counts mismatch: {file_name}"
            )
    extra_counts = sorted(str(key) for key in token_counts if key not in SESSION_PACK_FILES)
    if extra_counts:
        findings.append(
            f"{label}: session pack unlisted token_counts: {', '.join(extra_counts)}"
        )

    manifest_total = _non_bool_int(manifest.get("token_count_total"))
    if manifest_total is None:
        findings.append(f"{label}: session pack token_count_total invalid")
        return None
    observed_total = (
        sum(observed_token_counts[file_name] for file_name in SESSION_PACK_FILES)
        if set(observed_token_counts) == set(SESSION_PACK_FILES)
        else None
    )
    if valid_expected_counts and manifest_total != expected_total:
        findings.append(f"{label}: session pack token_count_total mismatch")
    if observed_total is not None and manifest_total != observed_total:
        findings.append(f"{label}: session pack token_count_total mismatch")
    return observed_total if observed_total is not None else manifest_total


def _check_session_pack_omission_report(
    root: Path,
    label: str,
    manifest_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    report_file = manifest.get("omission_report_file")
    if not isinstance(report_file, str) or not report_file:
        findings.append(f"{label}: session pack omission_report_file invalid")
        return
    report_path = (manifest_path.parent / report_file).resolve()
    try:
        report_path.relative_to(root.resolve())
        report_path.relative_to(manifest_path.parent.resolve())
    except ValueError:
        findings.append(f"{label}: session pack omission_report_file escapes pack")
        return
    if not report_path.is_file():
        findings.append(f"{label}: session pack omission_report_file missing")
        return
    expected_hash = manifest.get("omission_report_sha256")
    if not isinstance(expected_hash, str) or file_sha256(report_path) != expected_hash:
        findings.append(f"{label}: session pack omission_report_sha256 mismatch")


def _check_session_pack_blocking_contract(
    label: str,
    manifest: dict[str, Any],
    *,
    observed_total: int | None,
    findings: list[str],
) -> None:
    errors = _session_pack_error_list(label, manifest, findings)
    truncation_reasons = _session_pack_truncation_reasons(label, manifest, findings)
    blocked = manifest.get("blocked")
    token_budget = _non_bool_int(manifest.get("token_budget"))
    if token_budget is None or token_budget < 1:
        findings.append(f"{label}: session pack token_budget invalid")
    elif observed_total is not None and observed_total > token_budget:
        if blocked is not True:
            findings.append(
                f"{label}: session pack token budget exceeded without blocked"
            )
        _require_session_pack_error(
            label,
            errors,
            "session pack required context exceeds token budget",
            "required context over budget",
            findings,
        )
        _require_session_pack_truncation(
            label,
            truncation_reasons,
            "session_pack_required_context_exceeds_token_budget",
            "required context over budget",
            findings,
        )

    budget_omitted_ids = _session_pack_optional_string_list(
        label,
        manifest,
        "budget_omitted_episode_ids",
        findings,
    )
    if budget_omitted_ids:
        if blocked is not True:
            findings.append(
                f"{label}: session pack budget omissions without blocked"
            )
        _require_session_pack_error(
            label,
            errors,
            "session pack omitted available episodes due to token budget",
            "budget omission",
            findings,
        )
        _require_session_pack_truncation(
            label,
            truncation_reasons,
            "session_pack_token_budget_exceeded",
            "budget omission",
            findings,
        )

    unavailable_ids = _session_pack_optional_string_list(
        label,
        manifest,
        "unavailable_episode_ids",
        findings,
    )
    if unavailable_ids:
        _require_session_pack_error(
            label,
            errors,
            "session pack excluded future-unavailable episodes",
            "future-unavailable episode",
            findings,
        )
        _require_session_pack_truncation(
            label,
            truncation_reasons,
            "episode_available_from_after_cutoff",
            "future-unavailable episode",
            findings,
        )


def _session_pack_string_list_field(
    label: str,
    manifest: dict[str, Any],
    field: str,
    findings: list[str],
) -> list[str] | None:
    value = manifest.get(field)
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        findings.append(f"{label}: session pack {field} invalid")
        return None
    return list(value)


def _session_pack_optional_string_list(
    label: str,
    manifest: dict[str, Any],
    field: str,
    findings: list[str],
) -> list[str]:
    if field not in manifest:
        return []
    value = _session_pack_string_list_field(label, manifest, field, findings)
    return value or []


def _session_pack_hash_dict_field(
    label: str,
    manifest: dict[str, Any],
    field: str,
    findings: list[str],
) -> dict[str, str] | None:
    value = manifest.get(field)
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and key and isinstance(item, str) and item
        for key, item in value.items()
    ):
        findings.append(f"{label}: session pack {field} invalid")
        return None
    return dict(value)


def _session_pack_error_list(
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> list[str]:
    value = manifest.get("errors")
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        findings.append(f"{label}: session pack errors invalid")
        return []
    return list(value)


def _session_pack_truncation_reasons(
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> set[str]:
    value = manifest.get("truncations")
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        findings.append(f"{label}: session pack truncations invalid")
        return set()
    return {reason for item in value if isinstance(reason := item.get("reason"), str)}


def _require_session_pack_error(
    label: str,
    errors: list[str],
    expected: str,
    reason_label: str,
    findings: list[str],
) -> None:
    if expected not in errors:
        findings.append(f"{label}: session pack missing {reason_label} error")


def _require_session_pack_truncation(
    label: str,
    truncation_reasons: set[str],
    expected: str,
    reason_label: str,
    findings: list[str],
) -> None:
    if expected not in truncation_reasons:
        findings.append(f"{label}: session pack missing {reason_label} truncation")


def _estimate_session_pack_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


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
    source_mode = manifest.get("source_mode")
    allowed_categories = _training_export_allowed_categories(kind, source_mode)
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
    expected_sha = manifest.get("output_sha256")
    if not isinstance(expected_sha, str) or file_sha256(output_path) != expected_sha:
        findings.append(f"{label}: training export output_sha256 mismatch")
    rows = _read_training_export_rows(output_path, label, findings)
    if source_mode == "brain_records":
        source_record_hashes = _training_export_source_record_hashes(
            root,
            label,
            manifest,
            findings,
        )
        _check_training_export_record_scope(
            root,
            label,
            manifest,
            rows,
            source_record_hashes=source_record_hashes,
            findings=findings,
        )
        _check_training_export_record_rows(
            label,
            kind,
            allowed_categories,
            rows,
            source_record_hashes=source_record_hashes,
            findings=findings,
        )
    else:
        source_hashes = _training_export_source_hashes(root, label, manifest, findings)
        source_payloads = _training_export_source_payloads(root, source_hashes)
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
    _check_training_export_manifest_counts(
        label,
        kind,
        manifest,
        rows,
        allowed_categories,
        findings,
    )
    _check_training_export_phase_outputs(root, label, manifest, rows, findings)


def _training_export_allowed_categories(
    kind: str,
    source_mode: object,
) -> list[str] | None:
    categories = KIND_TRAINING_CATEGORIES.get(kind)
    if categories is None:
        return None
    allowed = list(categories)
    if kind == "sft" and source_mode == "brain_records":
        for category in RECORD_SFT_TRAINING_CATEGORIES:
            if category not in allowed:
                allowed.append(category)
    return allowed


def _training_export_source_record_hashes(
    root: Path,
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> dict[str, str]:
    raw = manifest.get("source_record_hashes")
    if not isinstance(raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in raw.items()
    ):
        findings.append(f"{label}: training export source_record_hashes invalid")
        return {}
    source_record_hashes = dict(raw)
    accepted_record_hashes = {
        record.record_id: record.normalized_payload_sha256
        for record in BrainRecordStore(root).list_records()
    }
    if source_record_hashes != accepted_record_hashes:
        findings.append(f"{label}: training export source_record_hashes mismatch")
    if manifest.get("source_record_count") != len(accepted_record_hashes):
        findings.append(f"{label}: training export source_record_count mismatch")
    return source_record_hashes


def _check_training_export_record_scope(
    root: Path,
    label: str,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    *,
    source_record_hashes: dict[str, str],
    findings: list[str],
) -> None:
    accepted_records = BrainRecordStore(root).list_records()
    accepted_record_ids = {record.record_id for record in accepted_records}
    source_record_ids = set(source_record_hashes)
    if source_record_ids != accepted_record_ids:
        findings.append(f"{label}: training export record source scope mismatch")

    episode_ids = _training_export_manifest_episode_ids(label, manifest, findings)
    record_episode_ids = {record.episode_id for record in accepted_records}
    if episode_ids is not None and set(episode_ids) != record_episode_ids:
        findings.append(f"{label}: training export record episode_ids mismatch")
    if manifest.get("episode_count") != len(record_episode_ids):
        findings.append(f"{label}: training export record episode_count mismatch")
    if manifest.get("source_episode_count") != len(record_episode_ids):
        findings.append(f"{label}: training export record source_episode_count mismatch")

    skipped_record_ids = _training_export_skipped_record_ids(label, manifest, findings)
    if skipped_record_ids is None:
        skipped_record_ids = set()
    if not skipped_record_ids <= source_record_ids:
        findings.append(f"{label}: training export skipped_record_ids mismatch")
    if manifest.get("skipped_record_count") != len(skipped_record_ids):
        findings.append(f"{label}: training export skipped_record_count mismatch")

    row_record_ids = {
        record_id
        for row in rows
        if isinstance(record_id := row.get("record_id"), str) and record_id
    }
    if not row_record_ids <= source_record_ids:
        findings.append(f"{label}: training export row record scope mismatch")
    if row_record_ids & skipped_record_ids:
        findings.append(f"{label}: training export skipped_record row overlap")
    if row_record_ids | skipped_record_ids != source_record_ids:
        findings.append(f"{label}: training export record coverage mismatch")


def _training_export_skipped_record_ids(
    label: str,
    manifest: dict[str, Any],
    findings: list[str],
) -> set[str] | None:
    raw = manifest.get("skipped_records")
    if not isinstance(raw, list):
        findings.append(f"{label}: training export skipped_records invalid")
        return None
    skipped_ids: list[str] = []
    invalid = False
    for item in raw:
        if not isinstance(item, dict):
            invalid = True
            continue
        record_id = item.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            invalid = True
            continue
        skipped_ids.append(record_id)
    if invalid:
        findings.append(f"{label}: training export skipped_records invalid")
    if len(skipped_ids) != len(set(skipped_ids)):
        findings.append(f"{label}: training export skipped_record_ids duplicate")
    return set(skipped_ids)


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


def _check_training_export_record_rows(
    label: str,
    kind: str,
    allowed_categories: list[str],
    rows: list[dict[str, Any]],
    *,
    source_record_hashes: dict[str, str],
    findings: list[str],
) -> None:
    for index, row in enumerate(rows, start=1):
        if row.get("schema_version") != "nslab.training_example.v2":
            findings.append(f"{label}: training export row {index} schema_version invalid")
        task = row.get("task")
        if not isinstance(task, str) or not task:
            findings.append(f"{label}: training export row {index} task invalid")
        category = row.get("training_category")
        if category not in allowed_categories:
            findings.append(f"{label}: training export row {index} category invalid")
        split = row.get("split")
        expected_split = _expected_training_export_record_split(kind)
        if split != expected_split:
            findings.append(f"{label}: training export row {index} split mismatch")
        _check_training_export_record_example_id(label, index, row, findings)
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
        _check_training_export_record_row_provenance(
            label,
            index,
            row,
            source_record_hashes=source_record_hashes,
            findings=findings,
        )


def _expected_training_export_record_split(kind: str) -> str:
    if kind == "sft":
        return "sft_records"
    if kind == "preference":
        return "preference"
    return "evals"


def _check_training_export_record_example_id(
    label: str,
    index: int,
    row: dict[str, Any],
    findings: list[str],
) -> None:
    example_id = row.get("example_id")
    task = row.get("task")
    split = row.get("split")
    record_id = row.get("record_id")
    if (
        not isinstance(example_id, str)
        or not example_id
        or not isinstance(task, str)
        or not task
        or not isinstance(split, str)
        or not split
        or not isinstance(record_id, str)
        or not record_id
    ):
        findings.append(f"{label}: training export row {index} example_id invalid")
        return
    expected = stable_id("TRN", split, task, record_id)
    if example_id != expected:
        findings.append(f"{label}: training export row {index} example_id mismatch")


def _check_training_export_record_row_provenance(
    label: str,
    index: int,
    row: dict[str, Any],
    *,
    source_record_hashes: dict[str, str],
    findings: list[str],
) -> None:
    provenance = row.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        findings.append(f"{label}: training export row {index} provenance missing")
        return
    if not all(isinstance(item, dict) for item in provenance):
        findings.append(f"{label}: training export row {index} provenance invalid")
        return
    record_id = row.get("record_id")
    episode_id = row.get("episode_id")
    if not isinstance(record_id, str) or not record_id:
        findings.append(f"{label}: training export row {index} record_id invalid")
        return
    if not isinstance(episode_id, str) or not episode_id:
        findings.append(f"{label}: training export row {index} episode_id invalid")
        return
    expected_hash = source_record_hashes.get(record_id)
    if expected_hash is None:
        findings.append(f"{label}: training export row {index} source_record_hash missing")
        return
    expected_uri = f"memory/records/{episode_id}.jsonl#{record_id}"
    if not any(
        entry.get("source_type") == "brain_record_provenance"
        and entry.get("uri") == expected_uri
        and entry.get("content_sha256") == expected_hash
        for entry in provenance
    ):
        findings.append(f"{label}: training export row {index} brain record provenance mismatch")


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
    allowed_categories: list[str],
    findings: list[str],
) -> None:
    if manifest.get("row_count") != len(rows):
        findings.append(f"{label}: training export row_count mismatch")
    if manifest.get("task_counts") != _training_task_counts(rows):
        findings.append(f"{label}: training export task_counts mismatch")
    if manifest.get("category_counts") != _training_category_counts(
        rows,
        allowed_categories=allowed_categories,
    ):
        findings.append(f"{label}: training export category_counts mismatch")
    if manifest.get("missing_training_categories") != _training_missing_categories(
        rows,
        allowed_categories=allowed_categories,
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


def _check_training_export_phase_outputs(
    root: Path,
    label: str,
    manifest: dict[str, Any],
    rows: list[dict[str, Any]],
    findings: list[str],
) -> None:
    raw = manifest.get("phase_outputs")
    if not isinstance(raw, dict):
        findings.append(f"{label}: training export phase_outputs invalid")
        return
    expected_phases = {"AUDIT_ONLY", "BLIND", "POSTMORTEM"}
    if set(raw) != expected_phases:
        findings.append(f"{label}: training export phase_outputs phase set mismatch")
    for phase in sorted(expected_phases):
        entry = raw.get(phase)
        if not isinstance(entry, dict):
            findings.append(f"{label}: training export phase output {phase} invalid")
            continue
        if entry.get("source_phase") != phase:
            findings.append(
                f"{label}: training export phase output {phase} source_phase mismatch"
            )
        expected_hindsight_safe = phase == "BLIND"
        if entry.get("hindsight_safe_for_blind_sft") is not expected_hindsight_safe:
            findings.append(
                f"{label}: training export phase output {phase} hindsight flag mismatch"
            )
        if phase == "AUDIT_ONLY" and entry.get("audit_only") is not True:
            findings.append(
                f"{label}: training export phase output {phase} audit_only flag mismatch"
            )
        output_file = entry.get("output_file")
        if not isinstance(output_file, str) or not output_file:
            findings.append(f"{label}: training export phase output {phase} output_file missing")
            continue
        if Path(output_file).is_absolute():
            findings.append(
                f"{label}: training export phase output {phase} output_file must be project-relative"
            )
        output_path = _resolve_training_export_output_path(root, output_file)
        if output_path is None:
            findings.append(
                f"{label}: training export phase output {phase} output_file escapes project root"
            )
            continue
        if not output_path.exists():
            findings.append(
                f"{label}: training export phase output {phase} output_file not found"
            )
            continue
        expected_sha = entry.get("output_sha256")
        if not isinstance(expected_sha, str) or file_sha256(output_path) != expected_sha:
            findings.append(
                f"{label}: training export phase output {phase} output_sha256 mismatch"
            )
        phase_rows = _read_training_export_rows(output_path, label, findings)
        expected_rows = (
            _training_audit_only_rows(manifest)
            if phase == "AUDIT_ONLY"
            else [row for row in rows if row.get("source_phase") == phase]
        )
        if entry.get("row_count") != len(expected_rows):
            findings.append(f"{label}: training export phase output {phase} row_count mismatch")
        if phase_rows != expected_rows:
            findings.append(f"{label}: training export phase output {phase} rows mismatch")


def _training_audit_only_rows(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    kind = manifest.get("kind")
    skipped_records = manifest.get("skipped_records")
    if not isinstance(kind, str) or not isinstance(skipped_records, list):
        return []
    rows: list[dict[str, Any]] = []
    for skipped in skipped_records:
        if not isinstance(skipped, dict):
            continue
        row = dict(skipped)
        row.update(
            {
                "schema_version": "nslab.training_audit_only_record.v1",
                "kind": kind,
                "source_phase": "AUDIT_ONLY",
                "hindsight_safe_for_blind_sft": False,
                "audit_only": True,
            }
        )
        rows.append(row)
    return rows


def _training_task_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        task = row.get("task")
        if isinstance(task, str):
            counts[task] = counts.get(task, 0) + 1
    return counts


def _training_category_counts(
    rows: list[dict[str, Any]],
    *,
    allowed_categories: list[str],
) -> dict[str, int]:
    counts = dict.fromkeys(allowed_categories, 0)
    for row in rows:
        category = row.get("training_category")
        if isinstance(category, str):
            counts[category] = counts.get(category, 0) + 1
    return counts


def _training_missing_categories(
    rows: list[dict[str, Any]],
    *,
    allowed_categories: list[str],
) -> list[str]:
    counts = _training_category_counts(rows, allowed_categories=allowed_categories)
    return [category for category in allowed_categories if counts.get(category, 0) == 0]


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
