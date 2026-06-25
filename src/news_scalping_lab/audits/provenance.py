"""Output provenance audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from news_scalping_lab.utils import canonical_json, read_json, sha256_text


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
        context_manifest_id = prediction.get("context_manifest_id")
        manifest = _check_context_manifest(root, path, context_manifest_id, findings)
        _check_report_link(root, path, context_manifest_id, findings)
        if manifest is not None:
            prompt_hashes = _check_manifest_basics(path, prediction, manifest, findings)
            if not isinstance(manifest.get("price_snapshot"), dict):
                findings.append(f"{path.name}: context manifest missing price_snapshot")
            if not isinstance(manifest.get("brain_file_hashes"), dict):
                findings.append(f"{path.name}: context manifest missing brain_file_hashes")
            _check_prompt_hash_traces(root, path, prompt_hashes, findings)
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
    return {
        "passed": not findings,
        "findings": findings,
        "checked_predictions": checked_predictions,
    }


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

    token_counts = manifest.get("token_counts", {})
    final_synthesis_was_run = (
        "final_synthesis" in prompt_hashes
        or isinstance(token_counts, dict)
        and "final_synthesis_prompt" in token_counts
    )
    if final_synthesis_was_run and not prompt_hashes.get("final_synthesis"):
        findings.append(f"{prediction_path.name}: context manifest missing final_synthesis prompt hash")
    return prompt_hashes


def _check_prompt_hash_traces(
    root: Path,
    prediction_path: Path,
    prompt_hashes: dict[str, Any],
    findings: list[str],
) -> None:
    purpose_by_hash_key = {
        "blind_analysis": "daily_blind_analysis",
        "red_team_candidate_review": "red_team_candidate_review",
        "final_synthesis": "final_synthesis",
    }
    traces_by_purpose = _trace_prompt_hashes_by_purpose(root, findings)
    for hash_key, purpose in purpose_by_hash_key.items():
        manifest_hash = prompt_hashes.get(hash_key)
        if not manifest_hash or purpose not in traces_by_purpose:
            continue
        if manifest_hash not in traces_by_purpose[purpose]:
            findings.append(
                f"{prediction_path.name}: prompt hash has no matching trace for {purpose}"
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


def _trace_prompt_hashes_by_purpose(root: Path, findings: list[str]) -> dict[str, set[str]]:
    traces: dict[str, set[str]] = {}
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
        traces.setdefault(purpose, set()).add(prompt_sha256)
    return traces


def _check_trace_payload(path: Path, payload: dict[str, Any], findings: list[str]) -> None:
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
