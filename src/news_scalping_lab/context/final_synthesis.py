"""Final synthesis context helpers."""

from __future__ import annotations

from typing import Any


def final_synthesis_input_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Return reproducibility counts for the exact final synthesis payload."""
    news_novelty = _dict_value(payload.get("news_novelty_review"))
    semantic = _dict_value(payload.get("additional_semantic_retrieval"))
    expansion = _dict_value(payload.get("open_world_candidate_expansion"))
    web_research = _dict_value(payload.get("web_research"))
    candidate_research = _dict_value(payload.get("candidate_research"))
    candidate_verification = _dict_value(payload.get("candidate_verification"))
    red_team_output = _dict_value(payload.get("red_team_output"))
    d_minus_one = _dict_value(payload.get("d_minus_one_market_data"))
    return {
        "required_input_count": _list_len(payload.get("required_inputs")),
        "current_news_count": _list_len(payload.get("current_news")),
        "first_pass_mechanism_count": _list_len(
            payload.get("open_world_first_analysis")
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


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _list_len(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
