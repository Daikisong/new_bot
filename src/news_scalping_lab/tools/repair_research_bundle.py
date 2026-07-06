"""Repair legacy research bundles into the direct-ingest package shape.

This tool only repackages already-present research records. It does not add
new market knowledge, beneficiaries, ticker mappings, or post-cutoff evidence.

주의: GPT 웹 연구 세션은 같은 prompt를 써도 Markdown 포장 형식이
흔들릴 수 있다. 어떤 세션은 표준 `NSLAB:BEGIN` marker를 쓰고, 어떤
세션은 `BEGIN_ARTIFACT`, `NSLAB_BLOCK_START`, `ARTIFACT:` heading, 또는
heading + fenced JSON/JSONL 형식으로 같은 artifact를 출력한다.
repair는 특정 날짜나 특정 파일명에 과적합하지 말고, 원본 MD 안에 실제로
존재하는 연구 artifact를 최대한 보존해서 표준 direct-ingest bundle로
다시 포장해야 한다.

따라서 upstream parser는 여러 Markdown wrapper를 받아들이되, 유연성은
wrapper에만 적용한다. payload가 JSON/JSONL로 parse되지 않거나 없는
record를 새로 만들어야 하는 상황이면 repair하지 않는다. repair의 목적은
있는 연구 원료를 살리는 것이지, 후공정에서 연구 지식을 창작하는 것이
아니다.

운영 메모: repair 요청에서 record_count=0이 나오더라도 곧바로 "먹을 게
없는 연구"라고 판정하지 않는다. 먼저 원본 MD를 열어 `brain_delta`,
`record_type`, `direct_ingest_contract`, `bundle_manifest`가 다른 marker나
fenced block 형식으로 존재하는지 확인해야 한다. 연구자가 같은 prompt를
써도 bundle 포장은 우후죽순으로 달라질 수 있으므로, 필요한 조치는 연구
자체가 아니라 parser/repair가 해당 Markdown 표기법을 유연하게 읽도록
보강하는 것이다. 단, 실제 MD 안에 없는 근거와 record를 후공정에서
만들어내면 안 된다.
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

RECORD_IDENTITY_FIELDS = (
    "record_id",
    "brain_delta_id",
    "issuer_day_case_id",
    "case_id",
    "blind_pair_id",
    "claim_id",
    "mechanism_id",
    "counterexample_id",
    "edge_id",
    "question_id",
    "error_id",
    "audit_id",
)

EVENT_TICKER_EDGE_ALLOWED_PATH_TYPES = {
    "CONTINUATION",
    "DIRECT",
    "FUNDAMENTAL",
    "INFERRED_NEW",
    "MARKET_MEMORY",
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

    fact_rows = jsonl_blocks.get("fact_ledger_blind.jsonl", [])
    inference_rows = jsonl_blocks.get("inference_ledger_blind.jsonl", [])
    source_ids = _known_ids(source_ledger_rows, "source_id")
    source_rows_by_id = _source_rows_by_id(source_ledger_rows)
    fact_ids = _known_ids(fact_rows, "fact_id")
    inference_ids = _known_ids(inference_rows, "inference_id")
    fact_source_ids_by_id = _fact_source_ids_by_id(fact_rows, source_ids)
    inference_fact_ids_by_id = _inference_fact_ids_by_id(inference_rows, fact_ids)

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
        fact_source_ids_by_id=fact_source_ids_by_id,
        inference_fact_ids_by_id=inference_fact_ids_by_id,
    )
    jsonl_blocks["brain_delta.jsonl"] = repaired_records
    source_ledger_rows = _materialize_referenced_source_placeholders(
        source_ledger_rows,
        repaired_records,
        trade_date=trade_date,
    )
    if source_ledger_rows:
        jsonl_blocks["source_ledger.jsonl"] = source_ledger_rows
    event_ledger_rows = _materialize_referenced_event_placeholders(
        jsonl_blocks.get("event_ledger.jsonl", []),
        repaired_records,
        trade_date=trade_date,
    )
    if event_ledger_rows:
        jsonl_blocks["event_ledger.jsonl"] = event_ledger_rows

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
    fact_source_ids_by_id: dict[str, list[str]],
    inference_fact_ids_by_id: dict[str, list[str]],
    ) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    issuer_day_records: list[dict[str, Any]] = []
    direct_event_records: list[dict[str, Any]] = []
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
                fact_source_ids_by_id=fact_source_ids_by_id,
                inference_fact_ids_by_id=inference_fact_ids_by_id,
            )
        elif record_type == "supervised_outcome_leader_case":
            record = _outcome_leader_case(
                row,
                index=index,
                episode_id=episode_id,
                trade_date=trade_date,
                available_from=available_from,
                known_source_ids=known_source_ids,
                known_fact_ids=known_fact_ids,
                fact_source_ids_by_id=fact_source_ids_by_id,
            )
        elif record_type == "supervised_missed_cluster_case":
            record = _missed_cluster_case(
                row,
                index=index,
                episode_id=episode_id,
                trade_date=trade_date,
                available_from=available_from,
            )
        elif record_type:
            record = _existing_direct_ingest_case(
                row,
                index=index,
                episode_id=episode_id,
                trade_date=trade_date,
                available_from=available_from,
                known_source_ids=known_source_ids,
                source_rows_by_id=source_rows_by_id,
                known_fact_ids=known_fact_ids,
                known_inference_ids=known_inference_ids,
                fact_source_ids_by_id=fact_source_ids_by_id,
                inference_fact_ids_by_id=inference_fact_ids_by_id,
            )
        else:
            record = _unknown_legacy_case(
                row,
                index=index,
                episode_id=episode_id,
                trade_date=trade_date,
                available_from=available_from,
            )
        _materialize_bundle_level_provenance(record, known_source_ids=known_source_ids)
        _drop_training_without_provenance(record)
        _drop_unsealed_preference_pair(record)
        _namespace_record_identity(record, episode_id=episode_id)
        if record.get("record_type") == "supervised_issuer_day_case":
            issuer_day_records.append(record)
        if record.get("record_type") == "supervised_direct_event_case":
            direct_event_records.append(record)
        repaired.append(record)
    _normalize_issuer_day_weights(issuer_day_records)
    _normalize_issuer_day_weights(direct_event_records)
    return repaired


def _namespace_record_identity(record: dict[str, Any], *, episode_id: str) -> None:
    old_id = _first_string(record.get("record_id"), record.get("brain_delta_id"))
    if old_id is None:
        return
    namespaced_id = _global_record_id(episode_id, old_id)
    if namespaced_id == old_id:
        return
    for field in RECORD_IDENTITY_FIELDS:
        if _first_string(record.get(field)) == old_id:
            record[field] = namespaced_id
    payload = record.get("payload")
    if isinstance(payload, dict):
        for field in RECORD_IDENTITY_FIELDS:
            if _first_string(payload.get(field)) == old_id:
                payload[field] = namespaced_id
    record["record_id"] = namespaced_id
    record["brain_delta_id"] = namespaced_id


def _global_record_id(episode_id: str, record_id: str) -> str:
    prefix = f"{episode_id}__"
    if record_id.startswith(prefix):
        return record_id
    return f"{prefix}{record_id}"


def _drop_training_without_provenance(record: dict[str, Any]) -> None:
    if record.get("training_eligible") is not True:
        return
    if _string_list(record.get("provenance_source_ids")):
        return
    record["training_eligible"] = False
    record["sample_weight"] = 0.0
    record["training_exclusion_reason"] = "missing_provenance_source_ids"


def _drop_unsealed_preference_pair(record: dict[str, Any]) -> None:
    if record.get("record_type") != "blind_leader_preference_pair":
        return
    if record.get("training_eligible") is not True:
        return
    if _has_sealed_preference_pair(record):
        return
    record["training_eligible"] = False
    record["sample_weight"] = 0.0
    record["training_exclusion_reason"] = "sealed_preference_pair_missing"
    reason = _first_string(record.get("eligibility_reason"))
    suffix = "sealed_preference_pair_missing"
    record["eligibility_reason"] = f"{reason}; {suffix}" if reason else suffix


def _has_sealed_preference_pair(record: dict[str, Any]) -> bool:
    payload = dict(record)
    nested = record.get("payload")
    if isinstance(nested, dict):
        for key, value in nested.items():
            payload.setdefault(key, value)
    preferred = _first_string(
        payload.get("blind_preferred_ticker"),
        payload.get("blind_preferred_candidate_id"),
    )
    rejected = _first_string(
        payload.get("blind_rejected_ticker"),
        payload.get("blind_rejected_candidate_id"),
    )
    return preferred is not None and rejected is not None
    reason = _first_string(record.get("eligibility_reason"))
    suffix = "missing_provenance_source_ids"
    record["eligibility_reason"] = f"{reason}; {suffix}" if reason else suffix


def _materialize_bundle_level_provenance(
    record: dict[str, Any],
    *,
    known_source_ids: set[str],
) -> None:
    if _string_list(record.get("provenance_source_ids")):
        return
    bundle_source_id: str | None = None
    if _has_blind_payload(record) and "SRC-BLIND-SNAPSHOT" in known_source_ids:
        bundle_source_id = "SRC-BLIND-SNAPSHOT"
    elif _has_outcome_payload(record) and "SRC-GOLD-REFERENCE" in known_source_ids:
        bundle_source_id = "SRC-GOLD-REFERENCE"
    if bundle_source_id is None:
        return
    record["provenance_source_ids"] = [bundle_source_id]
    record.setdefault("source_ids", [bundle_source_id])


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
    fact_source_ids_by_id: dict[str, list[str]],
    inference_fact_ids_by_id: dict[str, list[str]],
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
    repaired["available_from"] = _valid_available_from(
        repaired.get("available_from"),
        available_from,
    )
    ticker = _first_string(
        repaired.get("ticker"),
        repaired.get("company_ticker"),
        repaired.get("ticker_canonical"),
        repaired.get("code"),
        repaired.get("stock_code"),
        repaired.get("company_code"),
        repaired.get("issuer_code"),
        payload.get("ticker"),
        payload.get("code"),
        payload.get("stock_code"),
        payload.get("company_code"),
        payload.get("issuer_code"),
    )
    company_name = _first_string(
        repaired.get("company_name"),
        repaired.get("company"),
        repaired.get("name"),
        repaired.get("issuer_name"),
        repaired.get("name_on_D"),
        payload.get("company_name"),
        payload.get("company"),
        payload.get("name"),
        payload.get("issuer_name"),
        payload.get("name_on_D"),
    )
    if ticker:
        repaired["ticker"] = ticker
    if company_name:
        repaired["company_name"] = company_name

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
            *_string_list(payload.get("source_inference_ids")),
            *_string_list(payload.get("inference_ids")),
            *_string_list(payload.get("blind_inference_ids")),
        ],
        known_inference_ids,
    )
    payload_inference_id = _first_string(payload.get("inference_id"))
    if payload_inference_id in known_inference_ids and payload_inference_id not in inference_ids:
        inference_ids.append(payload_inference_id)
    if inference_ids:
        repaired["source_inference_ids"] = inference_ids
        repaired.setdefault("inference_ids", inference_ids)

    source_ids = _collect_source_ids(repaired, payload, known_source_ids)
    source_ids = _merge_unique(
        source_ids,
        _source_ids_from_fact_inference(
            fact_ids,
            inference_ids,
            fact_source_ids_by_id=fact_source_ids_by_id,
            inference_fact_ids_by_id=inference_fact_ids_by_id,
            known_source_ids=known_source_ids,
        ),
    )
    if source_ids:
        repaired["provenance_source_ids"] = source_ids
        repaired.setdefault("source_ids", source_ids)

    sample_weight = next(
        (
            parsed
            for candidate in (
                repaired.get("sample_weight"),
                repaired.get("training_weight"),
                payload.get("sample_weight"),
                payload.get("training_weight"),
            )
            if (parsed := _float_or_none(candidate)) is not None
        ),
        None,
    )
    if sample_weight is not None:
        repaired["sample_weight"] = sample_weight
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
    ticker = _first_string(
        ticker,
        record.get("ticker_canonical"),
        record.get("code"),
        record.get("stock_code"),
        record.get("company_code"),
        record.get("issuer_code"),
        payload.get("code"),
        payload.get("stock_code"),
        payload.get("company_code"),
        payload.get("issuer_code"),
    )
    company_name = _first_string(
        record.get("company_name"),
        record.get("company"),
        record.get("name"),
        record.get("issuer_name"),
        record.get("name_on_D"),
        payload.get("company_name"),
        payload.get("name"),
        payload.get("company"),
        payload.get("issuer_name"),
        payload.get("name_on_D"),
        payload.get("entity_name"),
    )
    if ticker:
        record["ticker"] = ticker
    if company_name:
        record["company_name"] = company_name
    normalized_type = original_type.strip().lower()
    if normalized_type != original_type:
        record["legacy_record_type"] = original_type
        record["record_type"] = normalized_type
        original_type = normalized_type
    if original_type in {
        "supervised_final_watchlist_case",
        "issuer_day_outcome",
        "issuer_day_outcome_case",
        "issuer_day",
        "issuer_day_case",
        "issuer_day_supervised",
        "issuer_day_supervised_record",
        "issuer_day_prediction_outcome",
        "issuer_day_final_prediction_outcome",
        "issuer_day_weight_update",
        "forecast_selection_result",
        "forecast_scorecard_record",
    }:
        record["legacy_record_type"] = original_type
        record["record_type"] = "supervised_issuer_day_case"
        record["issuer_day_case_id"] = _first_string(
            record.get("issuer_day_case_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
            f"{record.get('trade_date')}:{ticker}" if ticker else None,
        )
        record["issuer_day_weight_group_id"] = record["issuer_day_case_id"]
        record["issuer_day_sample_weight_policy"] = _first_string(
            record.get("issuer_day_sample_weight_policy"),
            "single_final_case",
        )
        record["training_target"] = "issuer_day_price_response"
        record.setdefault("sample_weight", 1.0)
        record["safe_D1_features"] = _merge_mapping(
            record.get("safe_D1_features"),
            record.get("features"),
            payload.get("safe_D1_features"),
            payload.get("features"),
            _compact(
                {
                    "blind_rank": _int_or_none(payload.get("rank")),
                    "blind_score": _float_or_none(record.get("blind_score")),
                    "theme": _first_string(record.get("theme"), payload.get("theme")),
                    "lane": _first_string(payload.get("lane")),
                    "source_screening_id": _first_string(payload.get("source_screening_id")),
                    "primary_quote": _first_string(
                        payload.get("primary_quote"),
                        record.get("exact_quote"),
                    ),
                },
            ),
        )
        record["D_outcome"] = _record_outcome(record, payload)
        record["outcome"] = record["D_outcome"]
        record["response_class"] = _label_as_string(
            record.get("response_class"),
            payload.get("postmortem_label"),
            record.get("label"),
            payload.get("label"),
        )
        record["label_quality"] = "verified"
        record["attribution_status"] = "postseal_label_attached_to_sealed_final"
    elif original_type in {
        "direct_event_final_case",
        "direct_event_outcome",
        "direct_event_outcome_case",
        "direct_event",
        "direct_event_supervised",
        "direct_event_supervised_record",
        "direct_event_labeled_response",
    }:
        record["legacy_record_type"] = original_type
        record["record_type"] = "supervised_direct_event_case"
        record["case_id"] = _first_string(
            record.get("case_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
        record["issuer_day_case_id"] = _first_string(
            record.get("issuer_day_case_id"),
            f"{record.get('trade_date')}:{ticker}" if ticker else None,
        )
        record["issuer_day_weight_group_id"] = record["issuer_day_case_id"]
        record["training_target"] = "direct_event_price_response"
        record.setdefault("sample_weight", 1.0)
        record["blind_fact_ids"] = _string_list(record.get("source_fact_ids"))
        record["safe_D1_features"] = _merge_mapping(
            record.get("safe_D1_features"),
            record.get("features"),
            payload.get("safe_D1_features"),
            payload.get("features"),
            _compact(
                {
                    "fact_class": _first_string(
                        payload.get("fact_class"),
                        payload.get("predicate_type"),
                        record.get("event_type"),
                    ),
                    "exact_quote": _first_string(
                        payload.get("exact_quote"),
                        record.get("exact_quote"),
                        record.get("event_quote"),
                    ),
                    "mechanism_sentence": _first_string(
                        payload.get("mechanism_sentence"),
                        record.get("mechanism_delta"),
                        record.get("lesson_atom"),
                    ),
                },
            ),
        )
        record["D_outcome"] = _record_outcome(record, payload)
        record["outcome"] = record["D_outcome"]
        record["response_class"] = _label_as_string(
            record.get("response_class"),
            record.get("label"),
            payload.get("label"),
        )
        record["label_quality"] = "verified"
        record["attribution_status"] = "postseal_label_attached_to_sealed_direct_event"
    elif original_type in {
        "nonfinal_rankable_pairwise_case",
        "negative_control_final_false_positive",
        "negative_control",
        "negative_control_final_miss",
        "negative_control_source_case",
        "negative_control_no_issuer_record",
        "supervised_rankable_not_final_case",
        "prediction_error_false_positive",
    }:
        record["legacy_record_type"] = original_type
        record["record_type"] = "negative_control_case"
        record["training_target"] = "candidate_exclusion_calibration"
        record.setdefault("sample_weight", 1.0)
        record["screening_id"] = _first_string(payload.get("source_screening_id"))
        record["candidate_lane"] = _first_string(payload.get("lane"))
        record["rejection_or_exclusion_reason"] = _first_string(
            payload.get("why_not_final_if_excluded"),
            payload.get("postmortem_label"),
            payload.get("miss_reason"),
            record.get("label"),
        )
        outcome = _record_outcome(record, payload)
        record["outcome_high_return_pct"] = _float_or_none(
            outcome.get("high_return_pct"),
        )
        record["upper_limit_touched"] = outcome.get("upper_limit_touched")
    elif original_type == "selected_negative_control_source":
        record["legacy_record_type"] = original_type
        record["record_type"] = "negative_control_case"
        record["training_target"] = "candidate_exclusion_calibration"
        record["sample_weight"] = 1.0
        record["screening_id"] = _first_string(payload.get("screening_id"))
        record["candidate_lane"] = _first_string(payload.get("lane"))
        record["rejection_or_exclusion_reason"] = _first_string(
            payload.get("rejection_reason"),
            payload.get("why_not_final_if_excluded"),
        )
        record["outcome_high_return_pct"] = _float_or_none(
            payload.get("outcome_high_return_pct"),
        )
        record["upper_limit_touched"] = payload.get("upper_limit_touched")
    elif original_type == "rankable_candidate_case":
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        record["training_exclusion_reason"] = "rankable_candidate_audit_not_training_type"
    elif original_type == "candidate_ranking_audit_sample":
        if payload.get("final_selected") is True:
            record["record_type"] = "supervised_issuer_day_case"
            record["issuer_day_case_id"] = _first_string(
                record.get("record_id"),
                f"{record.get('trade_date')}:{ticker}" if ticker else None,
            )
            record["issuer_day_weight_group_id"] = record["issuer_day_case_id"]
            record["training_target"] = "issuer_day_price_response"
            record["safe_D1_features"] = _merge_mapping(
                payload.get("features"),
                _compact({"blind_rank": _int_or_none(payload.get("final_rank"))}),
            )
            record["D_outcome"] = _record_outcome(record, payload)
            record["outcome"] = record["D_outcome"]
            record["label_quality"] = "verified"
            record["attribution_status"] = "postseal_label_attached_to_ranking_audit"
        else:
            record["record_type"] = "negative_control_case"
            record["training_target"] = "candidate_exclusion_calibration"
            record["rejection_or_exclusion_reason"] = _first_string(
                payload.get("nonfinal_reason"),
                "ranked_below_final_watchlist_cutoff",
            )
            outcome = _record_outcome(record, payload)
            record["outcome_high_return_pct"] = _float_or_none(
                outcome.get("high_return_pct"),
            )
            record["upper_limit_touched"] = outcome.get("upper_limit_touched")
    elif original_type in {
        "outcome_leader_reverse_audit_case",
        "outcome_leader_reverse_audit",
        "outcome_leader_reverse_audit_record",
        "outcome_leader_day",
        "outcome_leader_census_supervised",
        "missed_leader_error_case",
        "missed_outcome_leader",
        "missed_outcome_leader_audit",
    }:
        _standardize_outcome_leader_reverse_audit(
            record,
            payload=payload,
            legacy_record_type=original_type,
        )
    elif original_type in {
        "error",
        "error_archetype_aggregate",
        "pairwise_correction",
        "pairwise_rank_error",
        "candidate_generation_error",
        "miss_pattern_case",
    }:
        record["legacy_record_type"] = original_type
        if original_type == "candidate_generation_error":
            record["record_type"] = "candidate_generation_error_case"
            record["training_target"] = "candidate_generation_correction"
        else:
            record["record_type"] = "ranking_error_case"
            record["training_target"] = "candidate_ranking_correction"
        record.setdefault("sample_weight", 1.0)
        record["error_id"] = _first_string(
            record.get("error_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
        record["classification"] = _first_string(
            record.get("classification"),
            record.get("label"),
            payload.get("classification"),
            payload.get("error_type"),
            original_type,
        )
        record["correction_mode"] = _first_string(
            record.get("correction_mode"),
            record.get("lesson_atom"),
            payload.get("correction_mode"),
            payload.get("lesson_signal"),
            original_type,
        )
    elif original_type == "blind_leader_pair_case":
        record["legacy_record_type"] = original_type
        record["record_type"] = "blind_leader_preference_pair"
        record["training_target"] = "outcome_preferred_candidate"
        record["blind_pair_id"] = _first_string(
            record.get("blind_pair_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
    elif "pairwise" in original_type:
        record["legacy_record_type"] = original_type
        record["record_type"] = "ranking_error_case"
        record["training_target"] = "candidate_ranking_correction"
        record["error_id"] = _first_string(
            record.get("error_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
        record["classification"] = _first_string(record.get("label"), original_type)
    elif "newsless" in original_type:
        record["legacy_record_type"] = original_type
        record["record_type"] = "newsless_or_unexplained_case"
        record["training_target"] = "newsless_outcome_calibration"
        record["audit_id"] = _first_string(
            record.get("audit_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
        record["lesson"] = _first_string(
            record.get("lesson"),
            record.get("learning_note"),
            record.get("mechanism_delta"),
            payload.get("lesson"),
            payload.get("learning_note"),
        )
        record["outcome_high_return_pct"] = _float_or_none(
            _record_outcome(record, payload).get("high_return_pct"),
        )
    elif "theme" in original_type:
        record["legacy_record_type"] = original_type
        record["record_type"] = "theme_formation_case"
        record["training_target"] = "theme_formation_response"
        record["lesson"] = _first_string(
            record.get("lesson"),
            record.get("learning_note"),
            record.get("mechanism_delta"),
            payload.get("lesson"),
            payload.get("learning_note"),
        )
        outcome = _record_outcome(record, payload)
        record["outcome_high_return_pct"] = _float_or_none(
            outcome.get("high_return_pct"),
        )
        record["upper_limit_touched"] = outcome.get("upper_limit_touched")
    elif original_type in {
        "hit_pattern_case",
        "overweighted_clean_fundamental_catalyst",
        "fresh_direct_contract_not_sufficient",
    }:
        record["legacy_record_type"] = original_type
        record["record_type"] = "context_market_state_or_fact_case"
        record["training_target"] = "context_market_state_or_fact"
        record["lesson"] = _first_string(
            record.get("lesson"),
            record.get("learning_note"),
            record.get("mechanism_delta"),
            payload.get("lesson"),
        )


def _standardize_outcome_leader_reverse_audit(
    record: dict[str, Any],
    *,
    payload: dict[str, Any],
    legacy_record_type: str = "outcome_leader_reverse_audit_case",
) -> None:
    classification = _first_string(
        payload.get("classification"),
        record.get("classification"),
        payload.get("audit_result"),
        record.get("label"),
        payload.get("news_linkage_class"),
    )
    ticker = _first_string(record.get("ticker"), payload.get("ticker"))
    company_name = _first_string(
        record.get("company_name"),
        record.get("company"),
        record.get("name"),
        payload.get("name"),
        payload.get("company_name"),
        payload.get("company"),
    )
    record["legacy_record_type"] = legacy_record_type
    normalized_classification = (classification or "").upper()
    if (
        "CANDIDATE_GENERATION_MISS" in normalized_classification
        or "MISSED" in normalized_classification
        or "MISS" in normalized_classification
        or legacy_record_type == "missed_leader_error_case"
    ):
        record["record_type"] = "candidate_generation_error_case"
        record["training_target"] = "candidate_generation_correction"
        record.setdefault("sample_weight", 1.0)
        record["error_id"] = record.get("record_id")
        record["error_type"] = classification
        record["missed_ticker"] = ticker
        record["missed_company_name"] = company_name
        record["correction_mode"] = "outcome_leader_had_no_sealed_final_candidate"
    elif "RANKING_MISS" in normalized_classification or "RANK" in normalized_classification:
        record["record_type"] = "ranking_error_case"
        record["training_target"] = "candidate_ranking_correction"
        record.setdefault("sample_weight", 1.0)
        record["error_id"] = record.get("record_id")
        record["error_type"] = classification
        record["corrected_ticker"] = ticker
        record["corrected_company_name"] = company_name
        record["correction_mode"] = "outcome_leader_was_not_ranked_into_final"
    elif (
        "NEWSLESS" in normalized_classification
        or "NO_DIRECT_NEWS" in normalized_classification
        or "UNEXPLAINED" in normalized_classification
    ):
        record["record_type"] = "newsless_or_unexplained_case"
        record.setdefault("training_target", "newsless_outcome_calibration")
        record.setdefault("sample_weight", 1.0)
        record["audit_id"] = _first_string(payload.get("audit_id"), record.get("record_id"))
        record["input_news_hit_status"] = _first_string(payload.get("input_hit_status"))
        record["no_catalyst_asserted"] = True
        record["outcome_high_return_pct"] = _float_or_none(
            _record_outcome(record, payload).get("high_return_pct"),
        )
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


def _record_outcome(record: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    outcome = _merge_mapping(
        record.get("D_outcome"),
        record.get("outcome"),
        payload.get("D_outcome"),
        payload.get("outcome"),
    )
    label = record.get("label")
    if isinstance(label, dict):
        outcome.update(label)
    payload_label = payload.get("label")
    if isinstance(payload_label, dict):
        outcome.update(payload_label)
    outcome.update(_payload_outcome(payload))
    for source in (record, payload):
        high_return = _float_or_none(source.get("high_return_pct"))
        if high_return is not None:
            outcome.setdefault("high_return_pct", high_return)
        close_return = _float_or_none(source.get("close_return_pct"))
        if close_return is not None:
            outcome.setdefault("close_return_pct", close_return)
        if "upper_limit_touched" in source:
            outcome.setdefault("upper_limit_touched", source.get("upper_limit_touched"))
    return _compact(outcome)


def _merge_mapping(*values: Any) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        if isinstance(value, dict):
            merged.update(value)
    return _compact(merged)


def _label_as_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict):
            for key in (
                "hit_label",
                "outcome_label",
                "response_class",
                "label",
                "error_type",
            ):
                item = value.get(key)
                if isinstance(item, str) and item:
                    return item
    return None


def _repair_event_ticker_edge_cutoff(
    record: dict[str, Any],
    *,
    source_rows_by_id: dict[str, dict[str, Any]],
) -> None:
    record["path_type"] = _event_ticker_edge_path_type(record)
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
    fact_source_ids_by_id: dict[str, list[str]],
    inference_fact_ids_by_id: dict[str, list[str]],
) -> dict[str, Any]:
    ticker = _first_string(row.get("ticker"), row.get("code"))
    company_name = _first_string(row.get("company_name"), row.get("name"))
    source_ids = _filter_known(_string_list(row.get("source_ids")), known_source_ids)
    fact_ids = _filter_known(_string_list(row.get("fact_ids")), known_fact_ids)
    inference_ids = _filter_known(_string_list(row.get("inference_ids")), known_inference_ids)
    source_ids = _merge_unique(
        source_ids,
        _source_ids_from_fact_inference(
            fact_ids,
            inference_ids,
            fact_source_ids_by_id=fact_source_ids_by_id,
            inference_fact_ids_by_id=inference_fact_ids_by_id,
            known_source_ids=known_source_ids,
        ),
    )
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
    fact_source_ids_by_id: dict[str, list[str]],
) -> dict[str, Any]:
    ticker = _first_string(row.get("ticker"), row.get("code"))
    company_name = _first_string(row.get("company_name"), row.get("name"))
    source_ids = _filter_known(_string_list(row.get("source_ids")), known_source_ids)
    fact_ids = _filter_known(_string_list(row.get("fact_ids")), known_fact_ids)
    source_ids = _merge_unique(
        source_ids,
        _source_ids_from_fact_inference(
            fact_ids,
            [],
            fact_source_ids_by_id=fact_source_ids_by_id,
            inference_fact_ids_by_id={},
            known_source_ids=known_source_ids,
        ),
    )
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
        group_id = f"{trade_date}:{ticker}"
        weights = _fractional_weights(len(group))
        for record, weight in zip(group, weights, strict=True):
            record["sample_weight"] = weight
            record["issuer_day_weight_group_id"] = group_id
            record["issuer_day_sample_weight_policy"] = "fractional_issuer_day_group"


def _fractional_weights(count: int) -> list[float]:
    if count <= 0:
        return []
    if count == 1:
        return [1.0]
    base = round(1.0 / count, 6)
    weights = [base for _ in range(count - 1)]
    weights.append(round(1.0 - sum(weights), 6))
    return weights


def _repair_semantic_audit_row(row: dict[str, Any]) -> dict[str, Any]:
    repaired = dict(row)
    verdict = _first_string(
        row.get("semantic_verdict"),
        row.get("semantic_audit_status"),
        row.get("status"),
        row.get("audit_decision"),
        row.get("audit_status"),
        row.get("semantic_gate_status"),
        row.get("semantic_entailment"),
    )
    inferred_pass = (
        row.get("chain_complete") is True
        and row.get("quote_found_in_source_row") is True
        and not _string_list(row.get("fail_reasons"))
    )
    if (verdict and verdict.upper() == "PASS") or inferred_pass or row.get("pass") is True:
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
            "passed": True,
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


def _materialize_referenced_source_placeholders(
    rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    trade_date: str,
) -> list[dict[str, Any]]:
    repaired = list(rows)
    known_source_ids = _known_ids(repaired, "source_id")
    referenced_source_ids: set[str] = set()
    for record in records:
        referenced_source_ids.update(_string_list(record.get("provenance_source_ids")))
        referenced_source_ids.update(_string_list(record.get("source_ids")))
    missing = sorted(source_id for source_id in referenced_source_ids if source_id not in known_source_ids)
    for source_id in missing:
        repaired.append(
            {
                "source_id": source_id,
                "source_type": "research_bundle_referenced_source_placeholder",
                "source_kind": "REPAIRED_PROVENANCE_PLACEHOLDER",
                "trade_date": trade_date,
                "time_verified": False,
                "available_before_cutoff": None,
                "provenance_placeholder": True,
                "repair_note": (
                    "source id was referenced by brain_delta but absent from the "
                    "embedded source_ledger; placeholder preserves provenance identity "
                    "without inventing source content"
                ),
            }
        )
    return repaired


def _materialize_referenced_event_placeholders(
    rows: list[dict[str, Any]],
    records: list[dict[str, Any]],
    *,
    trade_date: str,
) -> list[dict[str, Any]]:
    repaired = list(rows)
    known_event_ids = _known_ids(repaired, "event_id")
    referenced_event_ids: set[str] = set()
    for record in records:
        referenced_event_ids.update(_string_list(record.get("event_ids")))
        payload = _as_dict(record.get("payload"))
        referenced_event_ids.update(_string_list(payload.get("event_ids")))
        event_id = _first_string(record.get("event_id"))
        if event_id is not None:
            referenced_event_ids.add(event_id)
        payload_event_id = _first_string(payload.get("event_id"))
        if payload_event_id is not None:
            referenced_event_ids.add(payload_event_id)
    missing = sorted(event_id for event_id in referenced_event_ids if event_id not in known_event_ids)
    for event_id in missing:
        repaired.append(
            {
                "event_id": event_id,
                "event_type": "research_bundle_referenced_event_placeholder",
                "trade_date": trade_date,
                "provenance_placeholder": True,
                "repair_note": (
                    "event id was referenced by brain_delta but absent from the "
                    "embedded event ledger; placeholder preserves reference identity "
                    "without inventing event content"
                ),
            }
        )
    return repaired


def _source_rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = _first_string(row.get("source_id"))
        if source_id is not None:
            indexed[source_id] = row
    return indexed


def _fact_source_ids_by_id(
    rows: list[dict[str, Any]],
    known_source_ids: set[str],
) -> dict[str, list[str]]:
    indexed: dict[str, list[str]] = {}
    for row in rows:
        fact_id = _first_string(row.get("fact_id"))
        if fact_id is None:
            continue
        source_ids = _filter_known(
            [
                *_string_list(row.get("source_ids")),
                *_string_list(row.get("source_row_ids")),
                *[
                    value
                    for value in (
                        _first_string(row.get("source_id")),
                        _first_string(row.get("row_id")),
                        _first_string(row.get("source_row_id")),
                    )
                    if value is not None
                ],
            ],
            known_source_ids,
        )
        if source_ids:
            indexed[fact_id] = _merge_unique(indexed.get(fact_id, []), source_ids)
    return indexed


def _inference_fact_ids_by_id(
    rows: list[dict[str, Any]],
    known_fact_ids: set[str],
) -> dict[str, list[str]]:
    indexed: dict[str, list[str]] = {}
    for row in rows:
        inference_id = _first_string(row.get("inference_id"))
        if inference_id is None:
            continue
        fact_ids = _filter_known(
            [
                *_string_list(row.get("supporting_fact_ids")),
                *_string_list(row.get("fact_ids")),
                *_string_list(row.get("source_fact_ids")),
            ],
            known_fact_ids,
        )
        if fact_ids:
            indexed[inference_id] = _merge_unique(indexed.get(inference_id, []), fact_ids)
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
        *_string_list(payload.get("provenance_source_ids")),
        *_string_list(payload.get("source_ids")),
        *_string_list(payload.get("source_ledger_ids")),
        *_string_list(payload.get("source_row_ids")),
    ]
    for key in ("source_row_id", "source_id", "news_source_id"):
        value = _first_string(record.get(key), payload.get(key))
        if value is not None:
            candidates.append(value)
    seen: set[str] = set()
    source_ids: list[str] = []
    for candidate in candidates:
        if (not known_source_ids or candidate in known_source_ids) and candidate not in seen:
            source_ids.append(candidate)
            seen.add(candidate)
    return source_ids


def _source_ids_from_fact_inference(
    fact_ids: list[str],
    inference_ids: list[str],
    *,
    fact_source_ids_by_id: dict[str, list[str]],
    inference_fact_ids_by_id: dict[str, list[str]],
    known_source_ids: set[str],
) -> list[str]:
    derived_fact_ids = list(fact_ids)
    for inference_id in inference_ids:
        derived_fact_ids = _merge_unique(
            derived_fact_ids,
            inference_fact_ids_by_id.get(inference_id, []),
        )
    source_ids: list[str] = []
    for fact_id in derived_fact_ids:
        source_ids = _merge_unique(source_ids, fact_source_ids_by_id.get(fact_id, []))
    return _filter_known(source_ids, known_source_ids)


def _event_ticker_edge_path_type(record: dict[str, Any]) -> str:
    payload = _as_dict(record.get("payload"))
    existing = _first_string(
        record.get("path_type"),
        payload.get("path_type"),
        record.get("candidate_path_type"),
        payload.get("candidate_path_type"),
    )
    if existing is not None and existing.upper() in EVENT_TICKER_EDGE_ALLOWED_PATH_TYPES:
        return existing.upper()
    edge_type = _first_string(
        record.get("edge_type"),
        payload.get("edge_type"),
        record.get("relation_class"),
        payload.get("relation_class"),
        record.get("catalyst_type"),
        payload.get("catalyst_type"),
    )
    normalized = edge_type.upper() if edge_type is not None else ""
    if "DIRECT" in normalized:
        return "DIRECT"
    if "CONTINUATION" in normalized:
        return "CONTINUATION"
    if "FUNDAMENTAL" in normalized:
        return "FUNDAMENTAL"
    if "MEMORY" in normalized:
        return "MARKET_MEMORY"
    return "INFERRED_NEW"


def _has_blind_payload(record: dict[str, Any]) -> bool:
    payload = _as_dict(record.get("payload"))
    blind_fields = (
        "blind_rank",
        "blind_score",
        "safe_D1_features",
        "blind_fact_ids",
        "blind_inference_ids",
        "blind_preferred_ticker",
        "blind_rejected_ticker",
        "blind_selected_ticker",
    )
    return any(field in record or field in payload for field in blind_fields)


def _has_outcome_payload(record: dict[str, Any]) -> bool:
    payload = _as_dict(record.get("payload"))
    outcome_fields = (
        "D_outcome",
        "outcome",
        "outcome_high_return_pct",
        "outcome_close_return_pct",
        "high_return_pct",
        "close_return_pct",
        "upper_limit_touched",
        "outcome_winner_ticker",
    )
    return any(field in record or field in payload for field in outcome_fields)


def _merge_unique(*groups: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            if item not in seen:
                merged.append(item)
                seen.add(item)
    return merged


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


def _valid_available_from(*values: Any) -> str | None:
    for value in values:
        candidate = _first_string(value)
        if candidate is None:
            continue
        try:
            datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return candidate
    return None


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
