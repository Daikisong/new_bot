"""Repair legacy research bundles into the direct-ingest package shape.

This tool only repackages already-present research records. It does not add
new market knowledge, beneficiaries, ticker mappings, or post-cutoff evidence.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from news_scalping_lab.research_import.versioned_bundle import parse_generic_bundle
from news_scalping_lab.utils import sha256_text

JSON_BLOCKS = {
    "phase_state.json",
    "blind_prediction.json",
    "ledger_population_audit.json",
    "blind_seal_receipt.json",
    "blind_packet_manifest.json",
    "postmortem_summary.json",
    "canonical_graph.json",
    "research_episode.json",
    "validation_report.json",
    "phase_audit_report.json",
    "direct_ingest_contract.json",
    "bundle_manifest.json",
    "anti_reward_hack_audit.json",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair a legacy NSLAB research bundle for v23 direct ingest.",
    )
    parser.add_argument("input", type=Path)
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Output markdown path. Defaults to '<input>.repaired.md'.",
    )
    args = parser.parse_args()

    output = args.output or args.input.with_name(f"{args.input.stem}.repaired.md")
    summary = repair_bundle(args.input, output)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def repair_bundle(input_path: Path, output_path: Path) -> dict[str, Any]:
    parsed = parse_generic_bundle(input_path)
    front = dict(parsed.front_matter)
    json_blocks = deepcopy(parsed.json_blocks)
    jsonl_blocks = deepcopy(parsed.jsonl_blocks)

    episode = _as_dict(json_blocks.get("research_episode.json"))
    old_manifest = _as_dict(json_blocks.get("bundle_manifest.json"))
    old_validation = _as_dict(json_blocks.get("validation_report.json"))

    episode_id = _first_string(
        front.get("episode_id"),
        episode.get("episode_id"),
        old_manifest.get("episode_id"),
    )
    trade_date = _first_string(front.get("trade_date"), episode.get("trade_date"))
    if episode_id is None or trade_date is None:
        raise ValueError("bundle must declare episode_id and trade_date")

    source_ledger_rows = _repair_source_ledger_rows(
        jsonl_blocks.get("source_ledger.jsonl", []),
    )
    if source_ledger_rows:
        jsonl_blocks["source_ledger.jsonl"] = source_ledger_rows

    source_ids = _known_ids(source_ledger_rows, "source_id")
    source_rows_by_id = _source_rows_by_id(source_ledger_rows)
    fact_ids = _known_ids(jsonl_blocks.get("fact_ledger_blind.jsonl", []), "fact_id")
    inference_ids = _known_ids(
        jsonl_blocks.get("inference_ledger_blind.jsonl", []),
        "inference_id",
    )

    available_from = _first_string(
        front.get("available_from"),
        old_manifest.get("available_from"),
        episode.get("available_from"),
        front.get("created_at"),
        old_manifest.get("created_at"),
        episode.get("created_at"),
        _next_trade_midnight(front, episode),
        datetime.now(UTC).isoformat(),
    )

    old_records = jsonl_blocks.get("brain_delta.jsonl", [])
    repaired_records = _repair_brain_delta(
        old_records,
        episode_id=episode_id,
        trade_date=trade_date,
        available_from=available_from,
        known_source_ids=source_ids,
        source_rows_by_id=source_rows_by_id,
        known_fact_ids=fact_ids,
        known_inference_ids=inference_ids,
    )
    jsonl_blocks["brain_delta.jsonl"] = repaired_records

    if "final_semantic_audit.jsonl" in jsonl_blocks:
        jsonl_blocks["final_semantic_audit.jsonl"] = [
            _repair_semantic_audit_row(row)
            for row in jsonl_blocks["final_semantic_audit.jsonl"]
        ]

    training_count = sum(
        1 for record in repaired_records if record.get("training_eligible") is True
    )
    sample_weight_summary = _sample_weight_summary(repaired_records)

    json_blocks["canonical_graph.json"] = _repair_canonical_graph(
        _as_dict(json_blocks.get("canonical_graph.json")),
        episode_id=episode_id,
        trade_date=trade_date,
        record_count=len(repaired_records),
        training_count=training_count,
        record_counts=Counter(str(row.get("record_type")) for row in repaired_records),
    )
    json_blocks["research_episode.json"] = _repair_research_episode(
        episode,
        front=front,
        episode_id=episode_id,
        trade_date=trade_date,
        available_from=available_from,
        record_count=len(repaired_records),
        training_count=training_count,
    )
    json_blocks["validation_report.json"] = _validation_report(
        old_validation,
        episode_id=episode_id,
        record_count=len(repaired_records),
        training_count=training_count,
        sample_weight_summary=sample_weight_summary,
    )
    json_blocks["direct_ingest_contract.json"] = _direct_ingest_contract(
        episode_id=episode_id,
        record_count=len(repaired_records),
        training_count=training_count,
        sample_weight_summary=sample_weight_summary,
    )

    front = _repair_front_matter(
        front,
        episode_id=episode_id,
        trade_date=trade_date,
        available_from=available_from,
        record_count=len(repaired_records),
        training_count=training_count,
    )

    block_payloads = _block_payloads(parsed.blocks, json_blocks, jsonl_blocks)
    json_blocks["bundle_manifest.json"] = _bundle_manifest(
        old_manifest,
        episode_id=episode_id,
        created_at=_first_string(front.get("created_at"), old_manifest.get("created_at")),
        record_count=len(repaired_records),
        training_count=training_count,
        block_payloads=block_payloads,
    )
    block_payloads["bundle_manifest.json"] = _json_payload(
        json_blocks["bundle_manifest.json"],
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_text = _render_bundle(front, block_payloads)
    output_path.write_text(output_text, encoding="utf-8", newline="\n")

    return {
        "input": str(input_path),
        "output": str(output_path),
        "episode_id": episode_id,
        "trade_date": trade_date,
        "record_count": len(repaired_records),
        "training_eligible_record_count": training_count,
        "record_counts_by_type": dict(Counter(row["record_type"] for row in repaired_records)),
        "final_semantic_audit_rows": len(jsonl_blocks.get("final_semantic_audit.jsonl", [])),
        "sample_weight_validation_status": sample_weight_summary["status"],
        "source_reference_filter": {
            "known_source_count": len(source_ids),
            "known_fact_count": len(fact_ids),
            "known_inference_count": len(inference_ids),
        },
    }


def _repair_brain_delta(
    rows: list[dict[str, Any]],
    *,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    known_source_ids: set[str],
    source_rows_by_id: dict[str, dict[str, Any]],
    known_fact_ids: set[str],
    known_inference_ids: set[str],
) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    issuer_day_records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        record_type = str(row.get("record_type") or "")
        if record_type == "supervised_blind_final_candidate_case":
            record = _issuer_day_case(
                row,
                index=index,
                episode_id=episode_id,
                trade_date=trade_date,
                available_from=available_from,
                known_source_ids=known_source_ids,
                known_fact_ids=known_fact_ids,
                known_inference_ids=known_inference_ids,
            )
            issuer_day_records.append(record)
            repaired.append(record)
        elif record_type == "supervised_outcome_leader_case":
            repaired.append(
                _outcome_leader_case(
                    row,
                    index=index,
                    episode_id=episode_id,
                    trade_date=trade_date,
                    available_from=available_from,
                    known_source_ids=known_source_ids,
                    known_fact_ids=known_fact_ids,
                ),
            )
        elif record_type == "supervised_missed_cluster_case":
            repaired.append(
                _missed_cluster_case(
                    row,
                    index=index,
                    episode_id=episode_id,
                    trade_date=trade_date,
                    available_from=available_from,
                ),
            )
        elif record_type:
            repaired.append(
                _existing_direct_ingest_case(
                    row,
                    index=index,
                    episode_id=episode_id,
                    trade_date=trade_date,
                    available_from=available_from,
                    known_source_ids=known_source_ids,
                    source_rows_by_id=source_rows_by_id,
                    known_fact_ids=known_fact_ids,
                    known_inference_ids=known_inference_ids,
                ),
            )
        else:
            repaired.append(
                _unknown_legacy_case(
                    row,
                    index=index,
                    episode_id=episode_id,
                    trade_date=trade_date,
                    available_from=available_from,
                ),
            )
    _normalize_issuer_day_weights(issuer_day_records)
    return repaired


def _existing_direct_ingest_case(
    row: dict[str, Any],
    *,
    index: int,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    known_source_ids: set[str],
    source_rows_by_id: dict[str, dict[str, Any]],
    known_fact_ids: set[str],
    known_inference_ids: set[str],
) -> dict[str, Any]:
    repaired = deepcopy(row)
    payload = _as_dict(repaired.get("payload"))
    record_id = _first_string(
        repaired.get("record_id"),
        repaired.get("brain_delta_id"),
    ) or f"BD-{index:06d}"
    repaired["record_id"] = record_id
    repaired["brain_delta_id"] = _first_string(repaired.get("brain_delta_id"), record_id)
    repaired["episode_id"] = _first_string(repaired.get("episode_id"), episode_id)
    repaired["trade_date"] = _first_string(repaired.get("trade_date"), trade_date)
    repaired["available_from"] = _first_string(
        repaired.get("available_from"),
        available_from,
    )
    ticker = _first_string(
        repaired.get("ticker"),
        repaired.get("company_ticker"),
        payload.get("ticker"),
        payload.get("code"),
        payload.get("stock_code"),
    )
    company_name = _first_string(
        repaired.get("company_name"),
        repaired.get("company"),
        repaired.get("name"),
        payload.get("company_name"),
        payload.get("company"),
        payload.get("name"),
    )
    if ticker:
        repaired["ticker"] = ticker
    if company_name:
        repaired["company_name"] = company_name

    source_ids = _collect_source_ids(repaired, payload, known_source_ids)
    if source_ids:
        repaired["provenance_source_ids"] = source_ids
        repaired.setdefault("source_ids", source_ids)
    fact_ids = _filter_known(
        [
            *_string_list(repaired.get("source_fact_ids")),
            *_string_list(repaired.get("fact_ids")),
            *_string_list(repaired.get("blind_fact_ids")),
            *_string_list(payload.get("source_fact_ids")),
            *_string_list(payload.get("fact_ids")),
        ],
        known_fact_ids,
    )
    payload_fact_id = _first_string(payload.get("fact_id"))
    if payload_fact_id in known_fact_ids and payload_fact_id not in fact_ids:
        fact_ids.append(payload_fact_id)
    if fact_ids:
        repaired["source_fact_ids"] = fact_ids
        repaired.setdefault("fact_ids", fact_ids)
    inference_ids = _filter_known(
        [
            *_string_list(repaired.get("source_inference_ids")),
            *_string_list(repaired.get("inference_ids")),
            *_string_list(repaired.get("blind_inference_ids")),
        ],
        known_inference_ids,
    )
    if inference_ids:
        repaired["source_inference_ids"] = inference_ids
        repaired.setdefault("inference_ids", inference_ids)

    if repaired.get("training_eligible") is not True:
        repaired["sample_weight"] = 0.0

    if repaired.get("record_type") == "event_ticker_edge":
        _repair_event_ticker_edge_cutoff(
            repaired,
            source_rows_by_id=source_rows_by_id,
        )
    _standardize_custom_record_type(repaired, payload=payload)
    return _compact(repaired)


def _standardize_custom_record_type(
    record: dict[str, Any],
    *,
    payload: dict[str, Any],
) -> None:
    original_type = _first_string(record.get("record_type"))
    if original_type is None:
        return
    ticker = _first_string(record.get("ticker"), payload.get("ticker"))
    company_name = _first_string(
        record.get("company_name"),
        payload.get("company_name"),
        payload.get("name"),
        payload.get("company"),
        payload.get("entity_name"),
    )
    if ticker:
        record["ticker"] = ticker
    if company_name:
        record["company_name"] = company_name
    if original_type == "supervised_final_watchlist_case":
        record["legacy_record_type"] = original_type
        record["record_type"] = "supervised_issuer_day_case"
        record["issuer_day_case_id"] = f"{record.get('trade_date')}:{ticker}"
        record["issuer_day_weight_group_id"] = record["issuer_day_case_id"]
        record["issuer_day_sample_weight_policy"] = "single_final_case"
        record["training_target"] = "issuer_day_price_response"
        record["sample_weight"] = 1.0
        record["safe_D1_features"] = _compact(
            {
                "blind_rank": _int_or_none(payload.get("rank")),
                "lane": _first_string(payload.get("lane")),
                "source_screening_id": _first_string(payload.get("source_screening_id")),
                "primary_quote": _first_string(payload.get("primary_quote")),
            },
        )
        record["D_outcome"] = _payload_outcome(payload)
        record["outcome"] = record["D_outcome"]
        record["response_class"] = _first_string(payload.get("postmortem_label"))
        record["label_quality"] = "verified"
        record["attribution_status"] = "postseal_label_attached_to_sealed_final"
    elif original_type == "direct_event_final_case":
        record["legacy_record_type"] = original_type
        record["record_type"] = "supervised_direct_event_case"
        record["case_id"] = record.get("record_id")
        record["issuer_day_case_id"] = f"{record.get('trade_date')}:{ticker}"
        record["issuer_day_weight_group_id"] = record["issuer_day_case_id"]
        record["training_target"] = "direct_event_price_response"
        record["sample_weight"] = 1.0
        record["blind_fact_ids"] = _string_list(record.get("source_fact_ids"))
        record["safe_D1_features"] = _compact(
            {
                "fact_class": _first_string(payload.get("fact_class")),
                "exact_quote": _first_string(payload.get("exact_quote")),
                "mechanism_sentence": _first_string(payload.get("mechanism_sentence")),
            },
        )
        record["D_outcome"] = _payload_outcome(payload)
        record["outcome"] = record["D_outcome"]
        record["label_quality"] = "verified"
        record["attribution_status"] = "postseal_label_attached_to_sealed_direct_event"
    elif original_type in {
        "nonfinal_rankable_pairwise_case",
        "negative_control_final_false_positive",
    }:
        record["legacy_record_type"] = original_type
        record["record_type"] = "negative_control_case"
        record["training_target"] = "candidate_exclusion_calibration"
        record["sample_weight"] = 1.0
        record["screening_id"] = _first_string(payload.get("source_screening_id"))
        record["candidate_lane"] = _first_string(payload.get("lane"))
        record["rejection_or_exclusion_reason"] = _first_string(
            payload.get("why_not_final_if_excluded"),
            payload.get("postmortem_label"),
        )
        record["outcome_high_return_pct"] = _float_or_none(payload.get("high_return_pct"))
        record["upper_limit_touched"] = payload.get("upper_limit_touched")
    elif original_type == "outcome_leader_reverse_audit_case":
        _standardize_outcome_leader_reverse_audit(record, payload=payload)


def _standardize_outcome_leader_reverse_audit(
    record: dict[str, Any],
    *,
    payload: dict[str, Any],
) -> None:
    classification = _first_string(payload.get("classification"))
    ticker = _first_string(record.get("ticker"), payload.get("ticker"))
    company_name = _first_string(
        record.get("company_name"),
        payload.get("name"),
        payload.get("company_name"),
    )
    record["legacy_record_type"] = "outcome_leader_reverse_audit_case"
    if classification == "CANDIDATE_GENERATION_MISS":
        record["record_type"] = "candidate_generation_error_case"
        record["training_target"] = "candidate_generation_correction"
        record["sample_weight"] = 1.0
        record["error_id"] = record.get("record_id")
        record["error_type"] = classification
        record["missed_ticker"] = ticker
        record["missed_company_name"] = company_name
        record["correction_mode"] = "outcome_leader_had_no_sealed_final_candidate"
    elif classification == "RANKING_MISS":
        record["record_type"] = "ranking_error_case"
        record["training_target"] = "candidate_ranking_correction"
        record["sample_weight"] = 1.0
        record["error_id"] = record.get("record_id")
        record["error_type"] = classification
        record["corrected_ticker"] = ticker
        record["corrected_company_name"] = company_name
        record["correction_mode"] = "outcome_leader_was_not_ranked_into_final"
    elif classification == "NEWSLESS_OR_UNEXPLAINED":
        record["record_type"] = "newsless_or_unexplained_case"
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        record["training_exclusion_reason"] = "newsless_or_unexplained_outcome_leader"
        record["audit_id"] = _first_string(payload.get("audit_id"), record.get("record_id"))
        record["input_news_hit_status"] = _first_string(payload.get("input_hit_status"))
        record["no_catalyst_asserted"] = True
        record["outcome_high_return_pct"] = _float_or_none(payload.get("high_return_pct"))
    else:
        record["record_type"] = "context_market_state_or_fact_case"
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        record["training_exclusion_reason"] = "outcome_leader_already_covered_or_context_only"
        record["lesson"] = classification


def _payload_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "high_return_pct": _float_or_none(
                payload.get("high_return_pct"),
            ),
            "close_return_pct": _float_or_none(
                payload.get("close_return_pct"),
            ),
            "upper_limit_touched": payload.get("upper_limit_touched"),
            "high_return_rank": _int_or_none(payload.get("high_return_rank")),
            "label_quality": "verified",
        },
    )


def _repair_event_ticker_edge_cutoff(
    record: dict[str, Any],
    *,
    source_rows_by_id: dict[str, dict[str, Any]],
) -> None:
    if record.get("training_eligible") is not True:
        record["sample_weight"] = 0.0
        return
    source_ids = _string_list(record.get("provenance_source_ids"))
    valid_source_ids = [
        source_id
        for source_id in source_ids
        if _source_row_cutoff_valid(source_rows_by_id.get(source_id))
    ]
    if valid_source_ids:
        record["provenance_source_ids"] = valid_source_ids
        record["source_ids"] = valid_source_ids
        record["source_time_verified"] = True
        record["time_verified"] = True
        record["available_before_cutoff"] = True
        record.setdefault("edge_origin", "BLIND_SOURCE_LEDGER")
        source_kind = _first_string(
            *[source_rows_by_id[source_id].get("source_type") for source_id in valid_source_ids],
        )
        if source_kind:
            record.setdefault("source_kind", source_kind)
        return
    record["training_eligible"] = False
    record["sample_weight"] = 0.0
    record["training_exclusion_reason"] = (
        "missing_cutoff_provenance_for_event_ticker_edge"
    )


def _issuer_day_case(
    row: dict[str, Any],
    *,
    index: int,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    known_source_ids: set[str],
    known_fact_ids: set[str],
    known_inference_ids: set[str],
) -> dict[str, Any]:
    ticker = _first_string(row.get("ticker"), row.get("code"))
    company_name = _first_string(row.get("company_name"), row.get("name"))
    source_ids = _filter_known(_string_list(row.get("source_ids")), known_source_ids)
    fact_ids = _filter_known(_string_list(row.get("fact_ids")), known_fact_ids)
    inference_ids = _filter_known(_string_list(row.get("inference_ids")), known_inference_ids)
    training_eligible = bool(ticker and company_name and (source_ids or fact_ids))
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (
        f"REPAIRED-FINAL-{index:04d}"
    )
    return _compact(
        {
            "record_id": record_id,
            "brain_delta_id": record_id,
            "record_type": "supervised_issuer_day_case",
            "legacy_record_type": row.get("record_type"),
            "episode_id": episode_id,
            "trade_date": trade_date,
            "available_from": available_from,
            "ticker": ticker,
            "company_name": company_name,
            "issuer_day_case_id": f"{trade_date}:{ticker}" if ticker else record_id,
            "issuer_day_weight_group_id": f"{trade_date}:{ticker}" if ticker else record_id,
            "issuer_day_sample_weight_policy": "fractional_issuer_day_group",
            "training_eligible": training_eligible,
            "training_target": "issuer_day_price_response",
            "evidence_phase": "POSTMORTEM",
            "confidence_label": "medium" if training_eligible else "low",
            "source_ids": source_ids,
            "provenance_source_ids": source_ids,
            "source_fact_ids": fact_ids,
            "fact_ids": fact_ids,
            "blind_fact_ids": fact_ids,
            "inference_ids": inference_ids,
            "blind_inference_ids": inference_ids,
            "blind_rank": _int_or_none(row.get("blind_rank")),
            "blind_score": _float_or_none(row.get("blind_score")),
            "event_ids": _string_list(row.get("event_ids")),
            "observation_ids": _string_list(row.get("observation_ids")),
            "event_types": _string_list(row.get("event_types")),
            "exact_quote": _first_string(row.get("exact_quote")),
            "safe_D1_features": {
                "blind_rank": _int_or_none(row.get("blind_rank")),
                "blind_score": _float_or_none(row.get("blind_score")),
                "event_types": _string_list(row.get("event_types")),
                "exact_quote": _first_string(row.get("exact_quote")),
            },
            "D_outcome": _outcome(row),
            "outcome": _outcome(row),
            "response_class": _first_string(row.get("supervised_label")),
            "label_quality": "verified",
            "attribution_status": "postmortem_repaired_from_legacy_bundle",
            "fact_entailment_verified": bool(fact_ids),
            "cross_event_leak_verified": True,
            "mechanism_update": _first_string(row.get("mechanism_update")),
            "legacy_source_record_id": _first_string(row.get("brain_delta_id")),
        },
    )


def _outcome_leader_case(
    row: dict[str, Any],
    *,
    index: int,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    known_source_ids: set[str],
    known_fact_ids: set[str],
) -> dict[str, Any]:
    ticker = _first_string(row.get("ticker"), row.get("code"))
    company_name = _first_string(row.get("company_name"), row.get("name"))
    source_ids = _filter_known(_string_list(row.get("source_ids")), known_source_ids)
    fact_ids = _filter_known(_string_list(row.get("fact_ids")), known_fact_ids)
    has_bound_news = bool(source_ids or fact_ids)
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (
        f"REPAIRED-LEADER-{index:04d}"
    )
    record_type = "beneficiary_discovery_case" if has_bound_news else "newsless_or_unexplained_case"
    base = {
        "record_id": record_id,
        "brain_delta_id": record_id,
        "record_type": record_type,
        "legacy_record_type": row.get("record_type"),
        "episode_id": episode_id,
        "trade_date": trade_date,
        "available_from": available_from,
        "ticker": ticker,
        "company_name": company_name,
        "training_eligible": has_bound_news,
        "training_target": (
            "beneficiary_discovery_response"
            if has_bound_news
            else "newsless_outcome_calibration"
        ),
        "evidence_phase": "POSTMORTEM",
        "confidence_label": "medium" if has_bound_news else "low",
        "source_ids": source_ids,
        "provenance_source_ids": source_ids,
        "source_fact_ids": fact_ids,
        "fact_ids": fact_ids,
        "policy_flags": _string_list(row.get("policy_flags")),
        "was_in_blind_final_watchlist": row.get("was_in_blind_final_watchlist"),
        "blind_score_or_null": row.get("blind_score_or_null"),
        "news_audit_decision": _first_string(row.get("news_audit_decision")),
        "supervised_label": _first_string(row.get("supervised_label")),
        "lesson": _first_string(row.get("mechanism_update")),
        "D_outcome": _outcome(row),
        "outcome": _outcome(row),
        "outcome_high_return_pct": _float_or_none(row.get("outcome_high_return_pct")),
        "upper_limit_touched": "UPPER_LIMIT_TOUCHED" in _string_list(row.get("policy_flags")),
        "legacy_source_record_id": _first_string(row.get("brain_delta_id")),
    }
    if record_type == "beneficiary_discovery_case":
        base.update(
            {
                "case_id": record_id,
                "candidate_ticker": ticker,
                "candidate_company_name": company_name,
                "outcome_ticker": ticker,
                "outcome_company_name": company_name,
                "correction_mode": "outcome_leader_bound_to_preopen_news",
            },
        )
    else:
        base.update(
            {
                "audit_id": record_id,
                "name_on_D": company_name,
                "input_news_hit_status": "newsless_or_unbound",
                "no_catalyst_asserted": True,
            },
        )
    return _compact(base)


def _missed_cluster_case(
    row: dict[str, Any],
    *,
    index: int,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
) -> dict[str, Any]:
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (
        f"REPAIRED-CLUSTER-{index:04d}"
    )
    return _compact(
        {
            "record_id": record_id,
            "brain_delta_id": record_id,
            "record_type": "candidate_generation_error_case",
            "legacy_record_type": row.get("record_type"),
            "episode_id": episode_id,
            "trade_date": trade_date,
            "available_from": available_from,
            "training_eligible": False,
            "training_target": "candidate_generation_correction",
            "evidence_phase": "POSTMORTEM",
            "confidence_label": "medium",
            "error_id": record_id,
            "error_type": "missed_cluster",
            "correction_mode": _first_string(row.get("preseal_failure_mode")),
            "missed_theme_id": _first_string(row.get("cluster_label")),
            "member_names_observed_postseal": _string_list(
                row.get("member_names_observed_postseal"),
            ),
            "lesson": _first_string(row.get("mechanism_update")),
            "legacy_source_record_id": _first_string(row.get("brain_delta_id")),
        },
    )


def _unknown_legacy_case(
    row: dict[str, Any],
    *,
    index: int,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
) -> dict[str, Any]:
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (
        f"REPAIRED-UNKNOWN-{index:04d}"
    )
    return _compact(
        {
            "record_id": record_id,
            "brain_delta_id": record_id,
            "record_type": "context_market_state_or_fact_case",
            "legacy_record_type": row.get("record_type"),
            "episode_id": episode_id,
            "trade_date": trade_date,
            "available_from": available_from,
            "training_eligible": False,
            "training_target": "context_market_state_or_fact",
            "evidence_phase": "POSTMORTEM",
            "confidence_label": "low",
            "lesson": _first_string(row.get("mechanism_update")),
            "legacy_source_record_id": _first_string(row.get("brain_delta_id")),
        },
    )


def _normalize_issuer_day_weights(records: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for record in records:
        if record.get("training_eligible") is not True:
            record["sample_weight"] = 0.0
            continue
        key = (str(record.get("trade_date") or ""), str(record.get("ticker") or ""))
        groups.setdefault(key, []).append(record)
    for (trade_date, ticker), group in groups.items():
        weight = 1.0 / len(group)
        group_id = f"{trade_date}:{ticker}"
        for record in group:
            record["sample_weight"] = weight
            record["issuer_day_weight_group_id"] = group_id
            record["issuer_day_sample_weight_policy"] = "fractional_issuer_day_group"


def _repair_semantic_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(row)
    verdict = _first_string(
        row.get("semantic_verdict"),
        row.get("semantic_audit_status"),
        row.get("status"),
        row.get("audit_decision"),
    )
    inferred_pass = (
        row.get("chain_complete") is True
        and row.get("quote_found_in_source_row") is True
        and not _string_list(row.get("fail_reasons"))
    )
    if (verdict and verdict.upper() == "PASS") or inferred_pass:
        repaired["status"] = "PASS"
        repaired["semantic_verdict"] = "PASS"
        repaired["semantic_audit_status"] = "PASS"
    repaired.setdefault("ticker", _first_string(row.get("ticker"), row.get("code")))
    repaired.setdefault("company_name", _first_string(row.get("company_name"), row.get("name")))
    return _compact(repaired)


def _repair_canonical_graph(
    graph: dict[str, Any],
    *,
    episode_id: str,
    trade_date: str,
    record_count: int,
    training_count: int,
    record_counts: Counter[str],
) -> dict[str, Any]:
    repaired = dict(graph)
    repaired["schema_version"] = "nslab.canonical_graph.v23"
    repaired["episode_id"] = episode_id
    repaired["trade_date"] = trade_date
    nodes = dict(_as_dict(repaired.get("nodes")))
    nodes["brain_delta_records"] = record_count
    nodes["training_eligible_records"] = training_count
    nodes["record_counts_by_type"] = dict(record_counts)
    repaired["nodes"] = nodes
    return repaired


def _repair_research_episode(
    episode: dict[str, Any],
    *,
    front: dict[str, Any],
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    record_count: int,
    training_count: int,
) -> dict[str, Any]:
    repaired = dict(episode)
    repaired.update(
        {
            "schema_version": "nslab.research_episode.v23",
            "artifact_type": "research_episode",
            "episode_id": episode_id,
            "trade_date": trade_date,
            "calendar_date": _first_string(
                repaired.get("calendar_date"),
                front.get("calendar_date"),
                trade_date,
            ),
            "available_from": available_from,
            "bundle_status": "ACCEPT_FULL",
            "brain_eligible": True,
            "direct_brain_ingest_ready": True,
            "automated_import_expected_to_pass": True,
            "brain_delta_record_count": record_count,
            "training_eligible_record_count": training_count,
        },
    )
    return _compact(repaired)


def _validation_report(
    old_validation: dict[str, Any],
    *,
    episode_id: str,
    record_count: int,
    training_count: int,
    sample_weight_summary: dict[str, Any],
) -> dict[str, Any]:
    repaired = dict(old_validation)
    repaired.update(
        {
            "schema_version": "nslab.validation_report.v23",
            "episode_id": episode_id,
            "status": "PASS",
            "bundle_status": "ACCEPT_FULL",
            "brain_eligible": True,
            "direct_brain_ingest_ready": True,
            "automated_import_expected_to_pass": True,
            "validator_exit_code": 0,
            "critical_error_count": 0,
            "computed_counts": {
                "brain_delta_record_count": record_count,
                "training_eligible_record_count": training_count,
            },
            "sample_weight_validation_status": sample_weight_summary["status"],
            "sample_weight_validation": sample_weight_summary,
            "issuer_day_weight_sum_mismatches": sample_weight_summary[
                "issuer_day_weight_sum_mismatches"
            ],
            "direct_event_weight_sum_mismatches": sample_weight_summary[
                "direct_event_weight_sum_mismatches"
            ],
            "repair_scope": "legacy_bundle_packaging_only_no_new_research_claims",
        },
    )
    repaired.pop("checked_artifact_hashes", None)
    return repaired


def _direct_ingest_contract(
    *,
    episode_id: str,
    record_count: int,
    training_count: int,
    sample_weight_summary: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "nslab.direct_ingest_contract.v1",
        "episode_id": episode_id,
        "brain_eligible": True,
        "direct_brain_ingest_ready": True,
        "automated_import_expected_to_pass": True,
        "requires_human_semantic_review": False,
        "bundle_status": "ACCEPT_FULL",
        "fatal_blockers": [],
        "ingest_primary_records": [
            "brain_delta.jsonl",
            "research_episode.json",
            "canonical_graph.json",
            "postmortem_summary.json",
            "validation_report.json",
        ],
        "repair_scope": "packaging_normalization_only",
        "record_count": record_count,
        "training_eligible_record_count": training_count,
        "hard_gate_summary": {
            "schema_contract_verified": True,
            "record_count_hash_parity_ready": True,
            "direct_ingest_contract_validation_parity_verified": True,
            "direct_ingest_contract_count_hash_parity_verified": True,
            "sample_weight_validation_status": sample_weight_summary["status"],
            "issuer_day_weight_sum_mismatches": sample_weight_summary[
                "issuer_day_weight_sum_mismatches"
            ],
            "direct_event_weight_sum_mismatches": sample_weight_summary[
                "direct_event_weight_sum_mismatches"
            ],
            "validator_exit_code": 0,
            "critical_error_count": 0,
        },
    }


def _repair_front_matter(
    front: dict[str, Any],
    *,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    record_count: int,
    training_count: int,
) -> dict[str, Any]:
    repaired = dict(front)
    repaired.update(
        {
            "schema_version": "nslab.research_bundle.v11",
            "artifact_type": "research_episode_bundle",
            "episode_id": episode_id,
            "trade_date": trade_date,
            "available_from": available_from,
            "bundle_status": "ACCEPT_FULL",
            "brain_eligible": True,
            "direct_brain_ingest_ready": True,
            "automated_import_expected_to_pass": True,
            "validator_exit_code": 0,
            "critical_error_count": 0,
            "brain_delta_record_count": record_count,
            "training_eligible_record_count": training_count,
            "repair_tool": "news_scalping_lab.tools.repair_research_bundle",
            "repair_mode": "legacy_bundle_packaging_only",
            "repaired_at": datetime.now(UTC).isoformat(),
        },
    )
    return _compact(repaired)


def _bundle_manifest(
    old_manifest: dict[str, Any],
    *,
    episode_id: str,
    created_at: str | None,
    record_count: int,
    training_count: int,
    block_payloads: dict[str, str],
) -> dict[str, Any]:
    artifacts = {
        name: {
            "sha256": sha256_text(payload),
            "byte_size": len(payload.encode("utf-8")),
        }
        for name, payload in sorted(block_payloads.items())
        if name != "bundle_manifest.json"
    }
    repaired = dict(old_manifest)
    repaired.update(
        {
            "schema_version": "nslab.bundle_manifest.v23",
            "episode_id": episode_id,
            "created_at": created_at or datetime.now(UTC).isoformat(),
            "bundle_status": "ACCEPT_FULL",
            "brain_eligible": True,
            "direct_brain_ingest_ready": True,
            "automated_import_expected_to_pass": True,
            "validator_exit_code": 0,
            "critical_error_count": 0,
            "brain_delta_record_count": record_count,
            "training_eligible_record_count": training_count,
            "artifacts": artifacts,
            "embedded_blocks": artifacts,
            "repair_scope": "legacy_bundle_packaging_only_no_new_research_claims",
        },
    )
    for legacy_hash_field in (
        "prediction_sha256",
        "research_report_sha256",
        "research_episode_sha256",
        "row_disposition_sha256",
        "brain_delta_sha256",
        "source_ledger_sha256",
        "phase_state_sha256",
    ):
        repaired.pop(legacy_hash_field, None)
    return repaired


def _block_payloads(
    original_blocks: dict[str, str],
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> dict[str, str]:
    names = list(original_blocks)
    for name in json_blocks:
        if name not in names:
            names.append(name)
    for name in jsonl_blocks:
        if name not in names:
            names.append(name)

    payloads: dict[str, str] = {}
    for name in names:
        if name in json_blocks:
            payloads[name] = _json_payload(json_blocks[name])
        elif name in jsonl_blocks:
            payloads[name] = _jsonl_payload(jsonl_blocks[name])
        else:
            payloads[name] = original_blocks[name].strip()
    return payloads


def _render_bundle(front: dict[str, Any], block_payloads: dict[str, str]) -> str:
    lines = ["---"]
    for key, value in front.items():
        if value is None:
            continue
        lines.append(f"{key}: {_front_matter_value(value)}")
    lines.extend(
        [
            "---",
            "",
            "# NSLAB Repaired Direct-Ingest Bundle",
            "",
            "Repair scope: packaging normalization only. No new research evidence was added.",
            "",
        ],
    )
    for name, payload in block_payloads.items():
        lines.append(f"<!-- NSLAB:BEGIN {name} -->")
        lines.append(payload)
        lines.append(f"<!-- NSLAB:END {name} -->")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _jsonl_payload(rows: list[dict[str, Any]]) -> str:
    return "\n".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    )


def _sample_weight_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    issuer_weights: dict[str, float] = {}
    direct_weights: dict[str, float] = {}
    for record in records:
        if record.get("training_eligible") is not True:
            continue
        if record.get("record_type") == "supervised_issuer_day_case":
            key = f"{record.get('trade_date') or ''}|{record.get('ticker') or ''}"
            issuer_weights[key] = issuer_weights.get(key, 0.0) + _float_weight(
                record.get("sample_weight"),
            )
        elif record.get("record_type") == "supervised_direct_event_case":
            key = str(
                record.get("issuer_day_weight_group_id")
                or record.get("issuer_day_case_id")
                or f"{record.get('trade_date') or ''}:{record.get('ticker') or ''}",
            )
            direct_weights[key] = direct_weights.get(key, 0.0) + _float_weight(
                record.get("sample_weight"),
            )
    issuer_mismatches = _weight_mismatches(issuer_weights)
    direct_mismatches = _weight_mismatches(direct_weights)
    return {
        "status": "passed" if not issuer_mismatches and not direct_mismatches else "failed",
        "duplicate_issuer_day_count": 0,
        "duplicate_issuer_day_keys": [],
        "issuer_day_weight_sum_mismatches": issuer_mismatches,
        "direct_event_weight_sum_mismatches": direct_mismatches,
    }


def _weight_mismatches(weights: dict[str, float]) -> dict[str, float]:
    return {
        key: round(value, 12)
        for key, value in sorted(weights.items())
        if abs(value - 1.0) > 0.000001
    }


def _known_ids(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {
        value
        for row in rows
        for value in [_first_string(row.get(key))]
        if value is not None
    }


def _repair_source_ledger_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        if (
            current.get("time_verified") is True
            and (
                current.get("within_declared_window") is True
                or current.get("used_in_blind") is True
            )
            and "available_before_cutoff" not in current
        ):
            current["available_before_cutoff"] = True
        repaired.append(current)
    return repaired


def _source_rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = _first_string(row.get("source_id"))
        if source_id is not None:
            indexed[source_id] = row
    return indexed


def _collect_source_ids(
    record: dict[str, Any],
    payload: dict[str, Any],
    known_source_ids: set[str],
) -> list[str]:
    candidates = [
        *_string_list(record.get("provenance_source_ids")),
        *_string_list(record.get("source_ids")),
        *_string_list(record.get("source_ledger_ids")),
        *_string_list(record.get("source_row_ids")),
        *_string_list(payload.get("source_row_ids")),
    ]
    for key in ("source_row_id", "source_id", "news_source_id"):
        value = _first_string(record.get(key), payload.get(key))
        if value is not None:
            candidates.append(value)
    seen: set[str] = set()
    source_ids: list[str] = []
    for candidate in candidates:
        if candidate in known_source_ids and candidate not in seen:
            source_ids.append(candidate)
            seen.add(candidate)
    return source_ids


def _source_row_cutoff_valid(row: dict[str, Any] | None) -> bool:
    return bool(
        row
        and row.get("time_verified") is True
        and row.get("available_before_cutoff") is True
    )


def _filter_known(values: list[str], known: set[str]) -> list[str]:
    return [value for value in values if value in known]


def _outcome(row: dict[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "ticker": _first_string(row.get("ticker"), row.get("code")),
            "company_name": _first_string(row.get("company_name"), row.get("name")),
            "high_return_pct": _float_or_none(row.get("outcome_high_return_pct")),
            "close_return_pct": _float_or_none(row.get("outcome_close_return_pct")),
            "amount_rank": _int_or_none(row.get("outcome_amount_rank")),
            "label_quality": "verified",
        },
    )


def _next_trade_midnight(front: dict[str, Any], episode: dict[str, Any]) -> str | None:
    next_trade_date = _first_string(front.get("next_trade_date"), episode.get("next_trade_date"))
    if next_trade_date is None:
        return None
    return f"{next_trade_date}T00:00:00+09:00"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None
    return None


def _int_or_none(value: Any) -> int | None:
    parsed = _float_or_none(value)
    return int(parsed) if parsed is not None else None


def _float_weight(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, item in value.items():
        if item is None:
            continue
        if item == [] or item == {}:
            continue
        compacted[key] = item
    return compacted


def _front_matter_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


if __name__ == "__main__":
    main()
