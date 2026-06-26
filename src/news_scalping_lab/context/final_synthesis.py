"""Final synthesis context helpers."""

from __future__ import annotations

from typing import Any

FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = (
    "current_news",
    "open_world_first_analysis",
    "news_novelty_review",
    "additional_semantic_retrieval",
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
RECORD_LEVEL_FINAL_SYNTHESIS_INPUTS = {
    "record_level_shard_contributions",
    "retrieved_records",
    "counterexample_records",
}
LEGACY_FINAL_SYNTHESIS_REQUIRED_INPUTS: tuple[str, ...] = tuple(
    item
    for item in FINAL_SYNTHESIS_REQUIRED_INPUTS
    if item not in RECORD_LEVEL_FINAL_SYNTHESIS_INPUTS
)


def final_synthesis_input_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return reproducibility counts for the exact final synthesis payload."""
    news_novelty = _dict_value(payload.get("news_novelty_review"))
    first_pass = _dict_value(payload.get("open_world_first_analysis"))
    semantic = _dict_value(payload.get("additional_semantic_retrieval"))
    expansion = _dict_value(payload.get("open_world_candidate_expansion"))
    web_research = _dict_value(payload.get("web_research"))
    candidate_research = _dict_value(payload.get("candidate_research"))
    candidate_verification = _dict_value(payload.get("candidate_verification"))
    red_team_output = _dict_value(payload.get("red_team_output"))
    d_minus_one = _dict_value(payload.get("d_minus_one_market_data"))
    summary = {
        "required_input_count": _list_len(payload.get("required_inputs")),
        "current_news_count": _list_len(payload.get("current_news")),
        "first_pass_mechanism_count": _first_pass_mechanism_count(
            payload.get("open_world_first_analysis"), first_pass
        ),
        "event_cluster_count": _list_len(payload.get("event_clusters")),
        "news_novelty_finding_count": _list_len(news_novelty.get("findings")),
        "semantic_retrieval_row_count": _list_len(semantic.get("rows")),
        "semantic_retrieval_episode_count": _list_len(semantic.get("episodes")),
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
    if "retrieved_records" in payload:
        summary["retrieved_record_count"] = _list_len(payload.get("retrieved_records"))
    if "counterexample_records" in payload:
        summary["counterexample_record_count"] = _list_len(
            payload.get("counterexample_records")
        )
    return summary


def final_synthesis_required_inputs_compatible(required_inputs: list[str]) -> bool:
    return tuple(required_inputs) in {
        FINAL_SYNTHESIS_REQUIRED_INPUTS,
        LEGACY_FINAL_SYNTHESIS_REQUIRED_INPUTS,
    }


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_pass_mechanism_count(value: Any, first_pass: dict[str, Any]) -> int:
    if first_pass:
        return _list_len(first_pass.get("mechanisms"))
    return _list_len(value)
