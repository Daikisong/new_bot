from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pipeline", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--news", type=Path, required=True)
    parser.add_argument("--outcome", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def front_matter(text: str) -> dict[str, str]:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise RuntimeError("front matter missing from first byte")
    result: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def main() -> None:
    args = parse_args()
    sys.path.insert(0, str(args.pipeline.resolve()))
    import common  # type: ignore

    text = args.bundle.read_text(encoding="utf-8")
    front = front_matter(text)
    blocks, marker_counts = common.parse_markdown_blocks(text)
    missing_or_duplicate = [
        name
        for name in common.REQUIRED_BLOCKS
        if marker_counts.get(name) != 1
    ]
    if missing_or_duplicate:
        raise RuntimeError(f"required marker block failure: {missing_or_duplicate}")
    parsed = {
        name: common.parse_block(name, blocks[name])
        for name in common.REQUIRED_BLOCKS
    }

    failures: list[str] = []

    def require(condition: bool, label: str) -> None:
        if not condition:
            failures.append(label)

    require(front.get("schema_version") == "nslab.research_bundle.v11", "front_schema")
    require(front.get("artifact_type") == "research_episode_bundle", "front_artifact_type")
    require(front.get("trade_date") == "2022-08-22", "front_trade_date")
    require(front.get("bundle_status") == "ACCEPT_FULL", "front_accept_full")
    require(front.get("brain_eligible", "").lower() == "true", "front_brain_eligible")
    require(front.get("direct_brain_ingest_ready", "").lower() == "true", "front_direct_ingest")

    news_rows = common.read_csv(args.news)
    outcome_rows = common.read_csv(args.outcome)
    source_rows = parsed["source_ledger.jsonl"]
    dispositions = parsed["row_disposition.jsonl"]
    queues = parsed["material_review_queue.jsonl"]
    reviews = parsed["material_review.jsonl"]
    facts = parsed["fact_ledger_blind.jsonl"]
    inferences = parsed["inference_ledger_blind.jsonl"]
    screenings = parsed["candidate_screening.jsonl"]
    witnesses = parsed["final_evidence_witness.jsonl"]
    prediction = parsed["blind_prediction.json"]
    outcome_ledger = parsed["outcome_ledger.jsonl"]
    leaders = parsed["outcome_leader_census.jsonl"]
    outcome_audits = parsed["outcome_to_news_audit.jsonl"]
    brain = parsed["brain_delta.jsonl"]
    closure = parsed["record_provenance_closure_audit.jsonl"]

    news_sources = [row for row in source_rows if row.get("source_type") == "NEWS_CSV_ROW"]
    require(len(news_sources) == len(news_rows), "source_ledger_news_count")
    require(len(dispositions) == len(news_rows), "row_disposition_count")
    expected_source_ids = {f"SRC-NEWS-{index:06d}" for index in range(1, len(news_rows) + 1)}
    require({row.get("source_id") for row in news_sources} == expected_source_ids, "source_ledger_id_set")
    require({row.get("source_row_id") for row in dispositions} == expected_source_ids, "row_disposition_id_set")

    queue_ids = {row.get("material_review_queue_id") for row in queues}
    review_queue_ids = {row.get("material_review_queue_id") for row in reviews}
    require(len(queues) == len(reviews), "material_queue_review_count")
    require(queue_ids == review_queue_ids, "material_queue_review_id_set")
    require(all(row.get("review_decision") for row in reviews), "material_review_decisions")
    require(all(row.get("exact_quote") and row.get("quote_found_in_source_row") is True for row in reviews), "material_review_quotes")
    require(all(row.get("issuer_binding") or row.get("rejection_reason") for row in reviews), "material_review_binding_or_rejection")

    review_ids = {row.get("material_review_id") for row in reviews}
    screening_review_ids: list[str] = []
    for row in screenings:
        screening_review_ids.extend(str(item) for item in row.get("source_material_review_ids", []))
    require(len(screenings) == len(reviews), "candidate_screening_material_count")
    require(set(screening_review_ids) == review_ids, "candidate_screening_material_id_set")
    require(len(screening_review_ids) == len(set(screening_review_ids)), "candidate_screening_duplicate_material")

    source_by_id = {row.get("source_id"): row for row in news_sources}
    fact_by_id = {row.get("fact_id"): row for row in facts}
    inference_by_id = {row.get("inference_id"): row for row in inferences}
    screening_by_id = {row.get("screening_id"): row for row in screenings}
    witness_by_candidate = {row.get("candidate_id"): row for row in witnesses}
    final_rows = prediction.get("final_watchlist", [])
    require(len(witnesses) == len(final_rows), "final_witness_count")
    require(all(row.get("semantic_verdict") == "PASS" for row in witnesses), "final_witness_pass")
    for final in final_rows:
        screen = screening_by_id.get(final.get("source_screening_id"))
        if not isinstance(screen, dict):
            failures.append(f"final_missing_screen:{final.get('candidate_id')}")
            continue
        fact_ids = screen.get("source_fact_ids", [])
        inf_ids = screen.get("source_inference_ids", [])
        require(bool(fact_ids), f"final_missing_fact:{final.get('candidate_id')}")
        require(bool(inf_ids), f"final_missing_inference:{final.get('candidate_id')}")
        witness = witness_by_candidate.get(final.get("candidate_id"))
        require(isinstance(witness, dict), f"final_missing_witness:{final.get('candidate_id')}")
        for fact_id in fact_ids:
            fact = fact_by_id.get(fact_id)
            require(isinstance(fact, dict), f"final_unknown_fact:{fact_id}")
            if isinstance(fact, dict):
                source = source_by_id.get(fact.get("source_row_id"))
                require(isinstance(source, dict), f"final_unknown_source:{fact.get('source_row_id')}")
                if isinstance(source, dict):
                    quote = str(fact.get("exact_quote") or "")
                    require(quote in f"{source.get('title', '')}\n{source.get('body', '')}", f"final_quote_not_in_source:{fact_id}")
        for inference_id in inf_ids:
            inference = inference_by_id.get(inference_id)
            require(isinstance(inference, dict), f"final_unknown_inference:{inference_id}")
            if isinstance(inference, dict):
                require(all(fid in fact_by_id for fid in inference.get("source_fact_ids", [])), f"final_inference_fact_chain:{inference_id}")

    require(len(outcome_ledger) == len(outcome_rows), "outcome_ledger_full_market_count")
    require(len(leaders) == len(outcome_audits), "outcome_leader_audit_count")
    require({row.get("outcome_leader_id") for row in leaders} == {row.get("outcome_leader_id") for row in outcome_audits}, "outcome_leader_audit_id_set")
    require(len(closure) == len(brain), "brain_closure_count")
    require(len(brain) > 0, "brain_delta_nonempty")

    known_fact_ids = set(fact_by_id)
    known_inference_ids = set(inference_by_id)
    known_source_ids = set(source_by_id)
    known_audit_ids = {row.get("audit_id") for row in outcome_audits}
    for record in brain:
        require(bool(record.get("record_type")), f"brain_record_type:{record.get('record_id')}")
        require(isinstance(record.get("payload"), dict), f"brain_payload:{record.get('record_id')}")
        require(all(fid in known_fact_ids for fid in record.get("source_fact_ids", [])), f"brain_fact_refs:{record.get('record_id')}")
        require(all(iid in known_inference_ids for iid in record.get("source_inference_ids", [])), f"brain_inference_refs:{record.get('record_id')}")
        require(all(sid in known_source_ids for sid in record.get("provenance_source_ids", [])), f"brain_source_refs:{record.get('record_id')}")
        require(all(aid in known_audit_ids for aid in record.get("outcome_audit_ids", [])), f"brain_audit_refs:{record.get('record_id')}")
        if record.get("training_eligible") is True:
            require(bool(record.get("source_fact_ids")), f"brain_training_fact:{record.get('record_id')}")
            require(bool(record.get("source_inference_ids")), f"brain_training_inference:{record.get('record_id')}")
            require(bool(record.get("provenance_source_ids")), f"brain_training_source:{record.get('record_id')}")

    validation = parsed["validation_report.json"]
    manifest = parsed["bundle_manifest.json"]
    direct = parsed["direct_ingest_contract.json"]
    require(validation.get("status") == "passed", "validation_status")
    require(validation.get("validator_exit_code") == 0, "validation_exit_code")
    require(validation.get("critical_error_count") == 0, "validation_critical_count")
    for key in (
        "brain_delta_payload_missing_count",
        "brain_delta_manifest_payload_count_mismatch_count",
        "brain_delta_declared_without_payload_count",
    ):
        require(validation.get(key) == 0, f"validation_{key}")
    require(validation.get("parsed_brain_delta_jsonl_row_count") == len(brain), "validation_brain_count")
    require(manifest.get("files", {}).get("brain_delta.jsonl", {}).get("row_count") == len(brain), "manifest_brain_count")
    require(manifest.get("brain_delta_count") == len(brain), "manifest_declared_brain_count")
    require(direct.get("brain_delta_count") == len(brain), "direct_brain_count")
    require(direct.get("direct_brain_ingest_ready") is True, "direct_ingest_ready")
    require(direct.get("automated_import_expected_to_pass") is True, "direct_automated_import")
    require(direct.get("brain_eligible") is True, "direct_brain_eligible")
    require(direct.get("fatal_blockers") == [], "direct_fatal_blockers")
    require(direct.get("requires_human_semantic_review") is False, "direct_human_review")

    blind_report = parsed["blind_report.md"]
    postmortem_report = parsed["postmortem_report.md"]
    for number in range(1, 20):
        require(re.search(rf"^## {number}\.\s", blind_report, re.M) is not None, f"blind_section_{number}")
    for number in range(20, 37):
        require(re.search(rf"^## {number}\.\s", postmortem_report, re.M) is not None, f"postmortem_section_{number}")

    if failures:
        raise RuntimeError("independent final validation failed: " + ", ".join(failures[:50]))

    receipt = {
        "schema_version": "nslab.independent_final_validation_receipt.v1",
        "status": "ACCEPT_FULL_INDEPENDENT_REOPEN_REPARSE_PASSED",
        "bundle": args.bundle.name,
        "bundle_sha256": hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
        "bundle_byte_size": args.bundle.stat().st_size,
        "csv_row_count": len(news_rows),
        "source_ledger_news_row_count": len(news_sources),
        "row_disposition_count": len(dispositions),
        "material_review_queue_count": len(queues),
        "material_reviewed_count": len(reviews),
        "candidate_screening_count": len(screenings),
        "final_watchlist_count": len(final_rows),
        "outcome_ledger_count": len(outcome_ledger),
        "outcome_leader_census_count": len(leaders),
        "outcome_to_news_audit_count": len(outcome_audits),
        "parsed_brain_delta_jsonl_row_count": len(brain),
        "brain_delta_record_type_counts": dict(Counter(str(row.get("record_type")) for row in brain)),
        "failure_count": 0,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
