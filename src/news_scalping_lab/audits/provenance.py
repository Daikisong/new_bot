"""Output provenance audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.contracts.models import CompanyMemory, MechanismMemory
from news_scalping_lab.utils import canonical_json, file_sha256, read_json, sha256_text

SEMANTIC_IMPORT_SOURCE_TYPE = "semantic_llm_structured_import"


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
            prompt_hashes = _check_manifest_basics(path, prediction, manifest, findings)
            if not isinstance(manifest.get("price_snapshot"), dict):
                findings.append(f"{path.name}: context manifest missing price_snapshot")
            if not isinstance(manifest.get("brain_file_hashes"), dict):
                findings.append(f"{path.name}: context manifest missing brain_file_hashes")
            _check_manifest_context_file_hashes(root, path, manifest, findings)
            _check_manifest_output_artifacts(root, path, manifest, findings)
            _check_manifest_model_config(path, manifest, findings)
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
    checked_company_memory_files = _check_company_memory_provenance(root, findings)
    checked_mechanism_memory_records = _check_mechanism_memory_provenance(root, findings)
    return {
        "passed": not findings,
        "findings": findings,
        "checked_predictions": checked_predictions,
        "checked_research_episode_files": checked_research_episode_files,
        "checked_company_memory_files": checked_company_memory_files,
        "checked_mechanism_memory_records": checked_mechanism_memory_records,
    }


def _check_research_episode_provenance(root: Path, findings: list[str]) -> int:
    checked = 0
    for path in _iter_research_episode_paths(root):
        episode = _read_json_object(path, findings)
        if episode is None or not _has_semantic_import_provenance(episode):
            continue
        checked += 1
        _check_semantic_import_audit(root, path, episode, findings)
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
    prediction_path: Path,
    prediction: dict[str, Any],
    manifest: dict[str, Any],
    findings: list[str],
) -> dict[str, Any]:
    prompt_hashes = manifest.get("prompt_hashes", {})
    if not isinstance(prompt_hashes, dict):
        findings.append(f"{prediction_path.name}: context manifest prompt_hashes is not an object")
        prompt_hashes = {}
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


def _check_manifest_output_artifacts(
    root: Path,
    prediction_path: Path,
    manifest: dict[str, Any],
    findings: list[str],
) -> None:
    _check_manifest_prediction_artifact(root, prediction_path, manifest, findings)
    _check_manifest_report_artifact(root, prediction_path, manifest, findings)


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
        _check_trace_model_config_matches_manifest(
            prediction_path,
            manifest,
            purpose,
            trace_metadata["model_configs"],
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
    for trace_model_config in trace_model_configs:
        mismatches = [
            key
            for key, expected_value in expected.items()
            if trace_model_config.get(key) != expected_value
        ]
        if mismatches:
            findings.append(
                f"{prediction_path.name}: trace model_config mismatch for {purpose}: "
                f"{', '.join(mismatches)}"
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
            {"prompt_hashes": set(), "model_configs": []},
        )
        trace_metadata["prompt_hashes"].add(prompt_sha256)
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
    elif status in {"ok", "checkpoint_hit"} and not isinstance(
        token_usage.get("prompt_tokens_estimate"), int
    ):
        findings.append(f"{path.name}: trace missing prompt token estimate")
    if status == "error" and not isinstance(payload.get("error"), dict):
        findings.append(f"{path.name}: error trace missing error details")


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
