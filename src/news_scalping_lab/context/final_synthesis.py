"""Final synthesis context helpers."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, cast

from news_scalping_lab.utils import canonical_json, sha256_text

FINAL_SYNTHESIS_V2_PROMPT_VERSION = "synthesis.final.v2"

FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = (
    "current_news",
    "open_world_first_analysis",
    "news_novelty_review",
    "additional_semantic_retrieval",
    "semantic_cluster_coverage",
    "open_world_candidate_expansion",
    "web_research",
    "global_brain",
    "all_shard_brains",
    "all_shard_contributions",
    "record_level_shard_contributions",
    "retrieved_raw_episodes",
    "retrieved_records",
    "positive_cases",
    "negative_cases",
    "positive_record_ids",
    "negative_record_ids",
    "counterexamples",
    "counterexample_records",
    "candidate_research",
    "candidate_web_checks",
    "candidate_verification",
    "red_team_output",
    "d_minus_one_market_data",
    "company_memory",
    "market_memory",
)
FINAL_SYNTHESIS_REQUIRED_INPUTS_V2: tuple[str, ...] = tuple(
    "memory_coverage_manifest"
    if item == "all_shard_contributions"
    else item
    for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
    if item != "record_level_shard_contributions"
)
FINAL_SYNTHESIS_V2_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "all_shard_contributions",
        "record_level_shard_contributions",
        "memory_sweep_artifacts",
        "record_sweep_artifacts",
        "record_sweep_artifact_hashes",
        "available_record_ids",
        "training_eligible_available_record_ids",
        "swept_record_ids",
    }
)


def phase2_memory_coverage_required(manifest: Mapping[str, Any]) -> bool:
    """Identify Phase 2 runs from configuration or persisted coverage evidence."""

    model_config = manifest.get("model_config")
    configured_v2 = (
        isinstance(model_config, Mapping)
        and model_config.get("final_synthesis_prompt_version")
        == FINAL_SYNTHESIS_V2_PROMPT_VERSION
    )
    return configured_v2 or any(
        manifest.get(field) is not None
        for field in (
            "memory_coverage_manifest_artifact",
            "memory_coverage_manifest_sha256",
            "memory_coverage_corpus_sha256",
        )
    )
RECORD_LEVEL_FINAL_SYNTHESIS_INPUTS = {
    "record_level_shard_contributions",
    "retrieved_records",
    "positive_record_ids",
    "negative_record_ids",
    "counterexample_records",
}
RECORD_ID_FINAL_SYNTHESIS_INPUTS = {
    "positive_record_ids",
    "negative_record_ids",
}
CLUSTER_COVERAGE_FINAL_SYNTHESIS_INPUTS = {
    "semantic_cluster_coverage",
}
PRE_RECORD_ID_FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = tuple(
    item
    for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
    if item not in RECORD_ID_FINAL_SYNTHESIS_INPUTS
)
LEGACY_FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = tuple(
    item
    for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
    if item not in RECORD_LEVEL_FINAL_SYNTHESIS_INPUTS
)
PRE_CLUSTER_COVERAGE_FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = tuple(
    item
    for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
    if item not in CLUSTER_COVERAGE_FINAL_SYNTHESIS_INPUTS
)
PRE_RECORD_ID_AND_CLUSTER_COVERAGE_FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = (
    tuple(
        item
        for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
        if item not in RECORD_ID_FINAL_SYNTHESIS_INPUTS
        and item not in CLUSTER_COVERAGE_FINAL_SYNTHESIS_INPUTS
    )
)
LEGACY_PRE_CLUSTER_COVERAGE_FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = tuple(
    item
    for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
    if item not in RECORD_LEVEL_FINAL_SYNTHESIS_INPUTS
    and item not in CLUSTER_COVERAGE_FINAL_SYNTHESIS_INPUTS
)


def final_synthesis_input_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return reproducibility counts for the exact final synthesis payload."""
    news_novelty = _dict_value(payload.get("news_novelty_review"))
    first_pass = _dict_value(payload.get("open_world_first_analysis"))
    semantic = _dict_value(payload.get("additional_semantic_retrieval"))
    cluster_coverage = _dict_value(payload.get("semantic_cluster_coverage"))
    expansion = _dict_value(payload.get("open_world_candidate_expansion"))
    web_research = _dict_value(payload.get("web_research"))
    candidate_research = _dict_value(payload.get("candidate_research"))
    candidate_verification = _dict_value(payload.get("candidate_verification"))
    red_team_output = _dict_value(payload.get("red_team_output"))
    d_minus_one = _dict_value(payload.get("d_minus_one_market_data"))
    memory_coverage = _dict_value(payload.get("memory_coverage_manifest"))
    summary: dict[str, Any] = {
        "required_input_count": _list_len(payload.get("required_inputs")),
        "current_news_count": _list_len(payload.get("current_news")),
        "first_pass_mechanism_count": _first_pass_mechanism_count(
            payload.get("open_world_first_analysis"), first_pass
        ),
        "event_cluster_count": _list_len(payload.get("event_clusters")),
        "news_novelty_finding_count": _list_len(news_novelty.get("findings")),
        "semantic_retrieval_row_count": _list_len(semantic.get("rows")),
        "semantic_retrieval_episode_count": _list_len(semantic.get("episodes")),
        "semantic_cluster_coverage_row_count": _list_len(cluster_coverage.get("rows")),
        "candidate_expansion_finding_count": _list_len(expansion.get("findings")),
        "web_source_count": _list_len(web_research.get("sources")),
        "candidate_web_check_count": _list_len(payload.get("candidate_web_checks")),
        "candidate_verification_finding_count": _list_len(
            candidate_verification.get("findings")
        ),
        "global_brain_file_count": _list_len(payload.get("global_brain")),
        "shard_brain_file_count": _list_len(payload.get("all_shard_brains")),
        "shard_contribution_count": _list_len(payload.get("all_shard_contributions")),
        "retrieved_raw_episode_count": _list_len(payload.get("retrieved_raw_episodes")),
        "positive_case_count": _list_len(payload.get("positive_cases")),
        "negative_case_count": _list_len(payload.get("negative_cases")),
        "counterexample_count": _list_len(payload.get("counterexamples")),
        "candidate_count": _list_len(candidate_research.get("candidates")),
        "red_team_finding_count": _list_len(red_team_output.get("candidate_findings")),
        "d_minus_one_snapshot_count": _list_len(d_minus_one.get("snapshots")),
        "company_memory_count": _list_len(payload.get("company_memory")),
        "market_memory_count": _list_len(payload.get("market_memory")),
    }
    if "record_level_shard_contributions" in payload:
        summary["record_shard_contribution_count"] = _list_len(
            payload.get("record_level_shard_contributions")
        )
    if memory_coverage:
        summary["memory_coverage_accepted_record_count"] = _int_value(
            memory_coverage.get("accepted_record_count")
        )
        summary["memory_coverage_available_record_count"] = _int_value(
            memory_coverage.get("available_record_count")
        )
        summary["memory_coverage_complete"] = (
            memory_coverage.get("coverage_complete") is True
        )
    if "accepted_record_count" in payload:
        summary["accepted_record_count"] = _int_value(
            payload.get("accepted_record_count")
        )
    if "available_record_count" in payload:
        summary["available_record_count"] = _int_value(
            payload.get("available_record_count")
        )
    if "available_record_ids" in payload:
        summary["available_record_id_count"] = _list_len(
            payload.get("available_record_ids")
        )
    if "training_eligible_available_record_count" in payload:
        summary["training_eligible_available_record_count"] = _int_value(
            payload.get("training_eligible_available_record_count")
        )
    if "training_eligible_available_record_ids" in payload:
        summary["training_eligible_available_record_id_count"] = _list_len(
            payload.get("training_eligible_available_record_ids")
        )
    if "swept_record_count" in payload:
        summary["swept_record_count"] = _int_value(
            payload.get("swept_record_count")
        )
    if "swept_record_ids" in payload:
        summary["swept_record_id_count"] = _list_len(
            payload.get("swept_record_ids")
        )
    if "missing_swept_record_ids" in payload:
        summary["missing_swept_record_id_count"] = _list_len(
            payload.get("missing_swept_record_ids")
        )
    if "unexpected_swept_record_ids" in payload:
        summary["unexpected_swept_record_id_count"] = _list_len(
            payload.get("unexpected_swept_record_ids")
        )
    if "duplicate_swept_record_ids" in payload:
        summary["duplicate_swept_record_id_count"] = _list_len(
            payload.get("duplicate_swept_record_ids")
        )
    if "retrieved_records" in payload:
        summary["retrieved_record_count"] = _list_len(payload.get("retrieved_records"))
    if "retrieved_record_ids" in payload:
        summary["retrieved_record_id_count"] = _list_len(
            payload.get("retrieved_record_ids")
        )
    if "excluded_retrieved_record_ids" in payload:
        summary["excluded_retrieved_record_id_count"] = _list_len(
            payload.get("excluded_retrieved_record_ids")
        )
    if "semantic_retrieval_record_ids" in payload:
        summary["semantic_retrieval_record_id_count"] = _list_len(
            payload.get("semantic_retrieval_record_ids")
        )
    if "excluded_semantic_retrieval_record_ids" in payload:
        summary["excluded_semantic_retrieval_record_id_count"] = _list_len(
            payload.get("excluded_semantic_retrieval_record_ids")
        )
    if "records" in semantic:
        summary["semantic_retrieval_record_count"] = _list_len(
            semantic.get("records")
        )
    if "included_record_ids" in semantic:
        summary["semantic_retrieval_included_record_id_count"] = _list_len(
            semantic.get("included_record_ids")
        )
    if "excluded_record_ids" in semantic:
        summary["semantic_retrieval_excluded_record_id_count"] = _list_len(
            semantic.get("excluded_record_ids")
        )
    if "semantic_cluster_coverage_ids" in payload:
        summary["semantic_cluster_coverage_id_count"] = _list_len(
            payload.get("semantic_cluster_coverage_ids")
        )
    if "semantic_cluster_coverage_missing_ids" in payload:
        summary["semantic_cluster_coverage_missing_id_count"] = _list_len(
            payload.get("semantic_cluster_coverage_missing_ids")
        )
    if "semantic_cluster_coverage_promoted_record_ids" in payload:
        summary["semantic_cluster_coverage_promoted_record_id_count"] = _list_len(
            payload.get("semantic_cluster_coverage_promoted_record_ids")
        )
    if "promoted_records" in cluster_coverage:
        summary["semantic_cluster_coverage_promoted_record_count"] = _list_len(
            cluster_coverage.get("promoted_records")
        )
    if "candidate_expansion_cluster_coverage_ids" in payload:
        summary["candidate_expansion_cluster_coverage_id_count"] = _list_len(
            payload.get("candidate_expansion_cluster_coverage_ids")
        )
    if "candidate_expansion_cluster_coverage_missing_ids" in payload:
        summary["candidate_expansion_cluster_coverage_missing_id_count"] = _list_len(
            payload.get("candidate_expansion_cluster_coverage_missing_ids")
        )
    if "candidate_expansion_audit_only_cluster_ids" in payload:
        summary["candidate_expansion_audit_only_cluster_id_count"] = _list_len(
            payload.get("candidate_expansion_audit_only_cluster_ids")
        )
    if "positive_record_ids" in payload:
        summary["positive_record_id_count"] = _list_len(
            payload.get("positive_record_ids")
        )
    if "negative_record_ids" in payload:
        summary["negative_record_id_count"] = _list_len(
            payload.get("negative_record_ids")
        )
    if "counterexample_records" in payload:
        summary["counterexample_record_count"] = _list_len(
            payload.get("counterexample_records")
        )
    if "counterexample_record_ids" in payload:
        summary["counterexample_record_id_count"] = _list_len(
            payload.get("counterexample_record_ids")
        )
    return summary


def final_synthesis_required_inputs_compatible(required_inputs: list[str]) -> bool:
    return tuple(required_inputs) in {
        FINAL_SYNTHESIS_REQUIRED_INPUTS_V2,
        FINAL_SYNTHESIS_REQUIRED_INPUTS,
        PRE_RECORD_ID_FINAL_SYNTHESIS_REQUIRED_INPUTS,
        LEGACY_FINAL_SYNTHESIS_REQUIRED_INPUTS,
        PRE_CLUSTER_COVERAGE_FINAL_SYNTHESIS_REQUIRED_INPUTS,
        PRE_RECORD_ID_AND_CLUSTER_COVERAGE_FINAL_SYNTHESIS_REQUIRED_INPUTS,
        LEGACY_PRE_CLUSTER_COVERAGE_FINAL_SYNTHESIS_REQUIRED_INPUTS,
    }


def final_synthesis_context_contract_verified(
    manifest: Mapping[str, Any],
    context: Mapping[str, Any],
) -> bool:
    schema_version = context.get("schema_version")
    if schema_version not in {
        "nslab.final_synthesis_context.v1",
        "nslab.final_synthesis_context.v2",
    }:
        return False
    manifest_run_id = manifest.get("run_id")
    if isinstance(manifest_run_id, str) and context.get("run_id") != manifest_run_id:
        return False
    payload = context.get("payload")
    if not isinstance(payload, dict):
        return False
    if context.get("payload_sha256") != sha256_text(canonical_json(payload)):
        return False
    required_inputs = payload.get("required_inputs")
    if not isinstance(required_inputs, list) or not all(
        isinstance(item, str) for item in required_inputs
    ):
        return False
    required_input_strings = cast(list[str], required_inputs)
    if context.get("required_inputs") != required_input_strings:
        return False
    if not final_synthesis_required_inputs_compatible(required_input_strings):
        return False
    if schema_version == "nslab.final_synthesis_context.v2":
        if tuple(required_input_strings) != FINAL_SYNTHESIS_REQUIRED_INPUTS_V2:
            return False
        if FINAL_SYNTHESIS_V2_FORBIDDEN_PAYLOAD_KEYS.intersection(payload):
            return False
        if not _memory_coverage_context_compatible(manifest, payload):
            return False
    missing_required_inputs = [
        key
        for key in required_input_strings
        if key not in payload
        and not _optional_missing_required_input_allowed(key, manifest)
    ]
    if missing_required_inputs:
        return False
    expected_summary = final_synthesis_input_summary(payload)
    if context.get("input_summary") != expected_summary:
        return False
    manifest_summary = manifest.get("final_synthesis_context_summary")
    if manifest_summary is not None and manifest_summary != expected_summary:
        return False
    return final_synthesis_price_context_compatible(
        manifest, payload
    ) and final_synthesis_manifest_record_metadata_compatible(manifest, payload)


def _memory_coverage_context_compatible(
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    coverage = payload.get("memory_coverage_manifest")
    if not isinstance(coverage, Mapping):
        return False
    if coverage.get("artifact_path") != manifest.get(
        "memory_coverage_manifest_artifact"
    ):
        return False
    if coverage.get("sha256") != manifest.get("memory_coverage_manifest_sha256"):
        return False
    if coverage.get("corpus_manifest_sha256") != manifest.get(
        "memory_coverage_corpus_sha256"
    ):
        return False
    if coverage.get("accepted_record_count") != manifest.get(
        "accepted_record_count"
    ):
        return False
    if coverage.get("available_record_count") != manifest.get(
        "available_record_count"
    ):
        return False
    return coverage.get("coverage_complete") is True


def _optional_missing_required_input_allowed(
    key: str,
    manifest: Mapping[str, Any],
) -> bool:
    return (
        key == "semantic_cluster_coverage"
        and "semantic_cluster_coverage_artifact" not in manifest
        and "semantic_cluster_coverage_summary" not in manifest
    )


def final_synthesis_manifest_record_metadata_compatible(
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    compact_coverage = "memory_coverage_manifest" in payload
    for field in (
        "available_record_ids",
        "training_eligible_available_record_ids",
        "swept_record_ids",
        "missing_swept_record_ids",
        "unexpected_swept_record_ids",
        "duplicate_swept_record_ids",
        "retrieved_record_ids",
        "excluded_retrieved_record_ids",
        "semantic_retrieval_record_ids",
        "excluded_semantic_retrieval_record_ids",
        "semantic_cluster_coverage_ids",
        "semantic_cluster_coverage_missing_ids",
        "semantic_cluster_coverage_promoted_record_ids",
        "candidate_expansion_cluster_coverage_ids",
        "candidate_expansion_uncovered_cluster_ids",
        "candidate_expansion_audit_only_cluster_ids",
        "counterexample_record_ids",
    ):
        if compact_coverage and field in {
            "available_record_ids",
            "training_eligible_available_record_ids",
            "swept_record_ids",
            "missing_swept_record_ids",
            "unexpected_swept_record_ids",
            "duplicate_swept_record_ids",
        }:
            continue
        if field not in manifest and field not in payload:
            continue
        if not _same_string_list_field(manifest, payload, field):
            return False
    for field in (
        "accepted_record_count",
        "available_record_count",
        "training_eligible_available_record_count",
        "swept_record_count",
    ):
        if compact_coverage and field in {
            "accepted_record_count",
            "available_record_count",
            "training_eligible_available_record_count",
            "swept_record_count",
        }:
            continue
        if field not in manifest and field not in payload:
            continue
        if not _same_nonnegative_int_field(manifest, payload, field):
            return False
    return True


def final_synthesis_price_context_compatible(
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> bool:
    price_snapshot = manifest.get("price_snapshot")
    market_data = payload.get("d_minus_one_market_data")
    if not isinstance(price_snapshot, Mapping) or not isinstance(market_data, Mapping):
        return False

    manifest_source_name = price_snapshot.get("source_name")
    if (
        not isinstance(manifest_source_name, str)
        or not manifest_source_name.strip()
        or market_data.get("source_name") != manifest_source_name
    ):
        return False

    manifest_source_ref = price_snapshot.get("source_ref")
    if (
        not isinstance(manifest_source_ref, str)
        or not manifest_source_ref.strip()
        or market_data.get("source_ref") != manifest_source_ref
    ):
        return False

    allowed_through = _date_value(price_snapshot.get("allowed_through"))
    if allowed_through is None:
        return False
    if market_data.get("allowed_through") != allowed_through.isoformat():
        return False

    trade_date = _date_value(manifest.get("trade_date"))
    snapshots = market_data.get("snapshots")
    if not isinstance(snapshots, list):
        return False
    for row in snapshots:
        if not isinstance(row, Mapping):
            return False
        row_trade_date = _date_value(row.get("trade_date"))
        if row_trade_date is None:
            return False
        if row_trade_date > allowed_through:
            return False
        if trade_date is not None and row_trade_date >= trade_date:
            return False
    return True


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _same_string_list_field(
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
    field: str,
) -> bool:
    manifest_value = manifest.get(field)
    payload_value = payload.get(field)
    if (
        not isinstance(manifest_value, list)
        or not all(isinstance(item, str) for item in manifest_value)
        or not isinstance(payload_value, list)
        or not all(isinstance(item, str) for item in payload_value)
    ):
        return False
    return manifest_value == payload_value


def _same_nonnegative_int_field(
    manifest: Mapping[str, Any],
    payload: Mapping[str, Any],
    field: str,
) -> bool:
    manifest_value = manifest.get(field)
    payload_value = payload.get(field)
    return (
        isinstance(manifest_value, int)
        and not isinstance(manifest_value, bool)
        and manifest_value >= 0
        and isinstance(payload_value, int)
        and not isinstance(payload_value, bool)
        and payload_value >= 0
        and manifest_value == payload_value
    )


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _int_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None


def _first_pass_mechanism_count(value: Any, first_pass: dict[str, Any]) -> int:
    if first_pass:
        return _list_len(first_pass.get("mechanisms"))
    return _list_len(value)
