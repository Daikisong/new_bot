"""Repair legacy research bundles into the direct-ingest package shape.

This tool only repackages already-present research records. It does not add
new market knowledge, beneficiaries, ticker mappings, or post-cutoff evidence.

필수 운영 계약 (MUST):
- 원본 연구 MD는 수십 분 이상 수행한 고비용 원본 자료다. 원본은 불변으로
  보존하고 repair 결과는 항상 별도 파일로 만든다.
- 파싱 실패, `record_count=0`, 낮은 training eligible 수, 알 수 없는 wrapper나
  필드명을 곧바로 "연구 내용 없음"으로 해석하지 않는다. 원본의 모든 artifact와
  산문을 먼저 확인해 parser/normalizer가 놓친 모집단인지 판별한다.
- 같은 prompt의 출력도 marker, heading, fence, alias, 중첩 위치, list/dict shape가
  달라질 수 있다. 처음 보는 형식은 해당 날짜만 때우지 말고 같은 구조 계열을
  읽는 범용 adapter/alias 규칙과 회귀 테스트로 보강한다.
- 기존 `brain_delta.jsonl`만 정규화하고 끝내지 않는다. 원본에 존재하는
  candidate screening/ranking, outcome reverse audit, fact/inference/source ledger를
  다시 대조해 만들 수 있었던 record 모집단이 빠졌는지 확인한다.
- 알려지지 않은 bundle version, record type, 필드, payload는 조용히 버리지 않는다.
  원형을 보존하거나 명시적으로 quarantine하고, 새 형식을 이해하지 못한 상태로
  성공을 선언하지 않는다.
- source/fact/inference/outcome 관계는 원본에 존재하는 ID와 값만 사용한다. 근거를
  창작하지 않으며, alias 정규화 뒤에도 provenance가 닫히지 않을 때만 record를
  보존한 채 training 제외 사유를 기록한다.
- repair 완료 조건은 record 손실 0, 누락 normalized record 0, orphan source/payload
  참조 0, hash 불일치 0, typed payload 오류 0, 모집단 underfill 0이다. 하나라도
  남으면 완성된 GOLD repair로 취급하지 않고 원본을 기준으로 parser를 보강한다.
- 후공정은 원본 한 파일씩 순차 진행한다. 새 변형을 발견하면 같은 구조 계열에 적용되는
  범용 규칙을 추가하고 현재 파일과 과거 fixture를 다시 실행한다. 현재 파일이 닫힌 뒤에만
  다음 파일로 넘어가며 날짜·종목·테마별 예외 하드코딩은 금지한다.

운영 메모:
GPT 연구 세션은 같은 prompt를 써도 Markdown 포장 방식이 날짜마다 흔들린다.
어떤 세션은 `NSLAB:BEGIN` marker를 쓰고, 어떤 세션은 `BEGIN_ARTIFACT`,
`NSLAB_BLOCK_START`, `ARTIFACT:`, `artifacts:`, `artifact_payload:` heading,
또는 heading + fenced JSON/JSONL 형식으로 같은 artifact를 출력한다.

따라서 repair는 특정 날짜나 특정 파일명에 과적합하면 안 된다. 원본 MD 안에
실제로 존재하는 `source_ledger`, `fact_ledger`, `candidate_screening`,
`outcome_ledger`, `brain_delta` 같은 연구 artifact를 최대한 보존해서 표준
direct-ingest bundle로 다시 포장해야 한다.

순차 후공정 원칙:
연구 MD 한 파일을 repair할 때 record_count=0, import_loss_audit_failed,
missing_payload_reference, marker parse error가 나오더라도 곧바로 "먹을 게 없는
연구"라고 단정하지 않는다. 먼저 원본 MD를 열어 `brain_delta`, `record_type`,
`direct_ingest_contract`, `bundle_manifest`가 다른 marker나 fenced block 형식으로
존재하는지 확인한다. 실제 연구 근거가 MD 안에 있으면 연구를 버리는 대신
parser/repair가 같은 구조 계열의 Markdown 표기법을 유연하게 읽도록 보강하고,
같은 파일을 재검증한 뒤 다음 파일로 넘어간다.

금지:
- 없는 source/fact/outcome을 새로 창작하기
- 주식, 테마, 수혜 관계를 코드에 하드코딩하기
- post-cutoff 또는 D-day outcome을 BLIND 근거처럼 보강하기

허용:
- wrapper/heading/fence 표기 차이를 표준 artifact block으로 정규화하기
- 이미 존재하는 fact/inference/source 관계를 따라 provenance를 복원하기
- source가 끝내 닫히지 않는 record를 버리지 않고 training_eligible=false로 내리기
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from news_scalping_lab.records.models import KNOWN_RECORD_PAYLOAD_MODELS
from news_scalping_lab.records.preference import has_sealed_preference_pair
from news_scalping_lab.research_import.repair_quality import (
    record_semantic_exclusion_relation_ids,
    semantic_exclusion_relation_ids,
)
from news_scalping_lab.research_import.repair_source_evidence import (
    rehydrate_news_source_timestamps,
)
from news_scalping_lab.research_import.versioned_bundle import parse_generic_bundle
from news_scalping_lab.utils import canonical_json, parse_datetime, sha256_text

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

_RANKABLE_SCREENING_DECISIONS = frozenset(
    {"INCLUDE", "WATCH", "WATCH_SECONDARY"}
)

_KNOWN_LEGACY_RECORD_TYPES = frozenset(
    {
        "blind_leader_pair_case",
        "blind_false_positive",
        "candidate_generation_error",
        "candidate_ranking_audit_sample",
        "counterfactual_pair",
        "cutline_exclusion_outcome",
        "direct_event",
        "direct_event_case",
        "direct_event_fact_outcome",
        "direct_event_final_case",
        "direct_event_hit_pattern",
        "direct_event_labeled_response",
        "direct_event_outcome",
        "direct_event_outcome_case",
        "direct_event_supervised",
        "direct_event_supervised_record",
        "error",
        "error_archetype_aggregate",
        "false_negative_outcome_leader",
        "forecast_scorecard_record",
        "forecast_selection_result",
        "fresh_direct_contract_not_sufficient",
        "hit_pattern_case",
        "issuer_day",
        "issuer_day_candidate_outcome",
        "issuer_day_case",
        "issuer_day_final_prediction_outcome",
        "issuer_day_final_watchlist_supervision",
        "issuer_day_outcome",
        "issuer_day_outcome_case",
        "issuer_day_prediction_outcome",
        "issuer_day_supervised",
        "issuer_day_supervised_record",
        "issuer_day_weight_update",
        "miss_pattern_case",
        "missed_leader_error_case",
        "missed_outcome_leader",
        "missed_outcome_leader_analysis",
        "missed_outcome_leader_audit",
        "final_candidate_outcome",
        "newsless_leader_control",
        "negative_control",
        "negative_control_final_false_positive",
        "negative_control_final_miss",
        "negative_control_no_issuer_record",
        "negative_control_source_case",
        "newsless_outcome_case",
        "newsless_outcome_leader_case",
        "nonfinal_rankable_pairwise_case",
        "outcome_leader_case",
        "outcome_leader_census_supervised",
        "outcome_leader_day",
        "outcome_leader_news_match",
        "outcome_leader_reverse_audit",
        "outcome_leader_reverse_audit_case",
        "outcome_leader_reverse_audit_record",
        "overweighted_clean_fundamental_catalyst",
        "pairwise_correction",
        "pairwise_rank_delta",
        "pairwise_rank_error",
        "pattern_delta",
        "prediction_error_false_positive",
        "ranking_error_pattern",
        "selected_low_response_error",
        "selected_negative_control_source",
        "semantic_guard_case",
        "supervised_final_watchlist_case",
        "supervised_rankable_not_final_case",
        "theme_case",
        "theme_outcome_case",
    }
)

_REPAIR_ONLY_RECORD_TYPES = frozenset({"rankable_candidate_case"})


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
    parser.add_argument(
        "--news-csv-root",
        type=Path,
        default=Path("docs/csv"),
        help="Root containing source news CSV files used for SHA-bound timestamp repair.",
    )
    args = parser.parse_args()

    output = args.output or args.input.with_name(f"{args.input.stem}.repaired.md")
    summary = repair_bundle(
        args.input,
        output,
        news_csv_root=args.news_csv_root,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))


def repair_bundle(
    input_path: Path,
    output_path: Path,
    *,
    news_csv_root: Path | None = None,
) -> dict[str, Any]:
    # Keep this pipeline ordered. It first normalizes only source-present
    # evidence and identifiers, then repairs record references/eligibility, and
    # only after that regenerates manifests and hashes. Moving manifest repair
    # earlier can make stale counts or hashes appear authoritative.
    if input_path.resolve() == output_path.resolve():
        raise ValueError("repair output must not overwrite the source bundle")
    parsed = parse_generic_bundle(input_path)
    front = dict(parsed.front_matter)
    json_blocks = deepcopy(parsed.json_blocks)
    jsonl_blocks = deepcopy(parsed.jsonl_blocks)

    episode = _as_dict(json_blocks.get("research_episode.json"))
    old_manifest = _as_dict(json_blocks.get("bundle_manifest.json"))
    old_validation = _as_dict(json_blocks.get("validation_report.json"))
    quarantine_status = _declared_quarantine_status(front)

    episode_id = _first_string(
        front.get("episode_id"),
        episode.get("episode_id"),
        old_manifest.get("episode_id"),
    )
    trade_date = _first_string(
        front.get("trade_date"),
        front.get("calendar_date"),
        front.get("date"),
        episode.get("trade_date"),
        episode.get("calendar_date"),
        old_manifest.get("trade_date"),
        old_manifest.get("calendar_date"),
    )
    episode_id_derived = False
    if trade_date is None:
        raise ValueError("bundle must declare trade_date")
    if episode_id is None:
        # A few legacy bundles contain a complete dated research package but
        # omit only the episode metadata.  Derive a stable namespace from the
        # source bytes rather than inventing a date/ticker-specific identity.
        episode_id = _derive_episode_id(input_path, trade_date)
        episode_id_derived = True

    # CSV timestamp rehydration is SHA/source-row bound and cutoff checked. It
    # must never become a route for adding news or post-cutoff evidence that was
    # absent from the original bundle.
    source_ledger_rows = _repair_source_ledger_rows(
        jsonl_blocks.get("source_ledger.jsonl", []),
    )
    input_audit = _as_dict(json_blocks.get("input_audit.json"))
    source_ledger_rows, timestamp_repair_summary = rehydrate_news_source_timestamps(
        source_ledger_rows,
        news_csv_root=news_csv_root,
        cutoff_at=_bundle_cutoff(front, json_blocks),
        declared_input_file=_first_string(
            front.get("input_file"),
            input_audit.get("input_file"),
        ),
        declared_input_sha256=_first_string(
            front.get("input_sha256"),
            input_audit.get("input_sha256"),
        ),
    )
    if source_ledger_rows:
        jsonl_blocks["source_ledger.jsonl"] = source_ledger_rows

    # Some legacy bundles use the same news source row as ``SRC-000123`` in
    # fact/inference references while the source ledger calls it
    # ``SRC-NEWS-000123`` (or ``SRC-NEWS-ROW-000123``).  Resolve only an
    # unambiguous numeric-suffix alias already present in the source ledger;
    # never manufacture a source or infer a mapping from a ticker/title.
    reference_rows = [
        row
        for rows in jsonl_blocks.values()
        if isinstance(rows, list)
        for row in rows
        if isinstance(row, dict)
    ]
    reference_aliases = {
        **_source_reference_aliases(source_ledger_rows, reference_rows=reference_rows),
        **_material_review_reference_aliases(
            jsonl_blocks.get("material_review.jsonl", []),
        ),
    }
    if reference_aliases:
        json_blocks = _normalize_source_reference_aliases(
            json_blocks,
            reference_aliases,
        )
        jsonl_blocks = _normalize_source_reference_aliases(
            jsonl_blocks,
            reference_aliases,
        )
    _normalize_outcome_identity_aliases(jsonl_blocks)
    _materialize_missing_material_review_rows(jsonl_blocks)

    # Legacy bundles may carry a second, source-backed postmortem ledger.  It
    # is still the bundle's own evidence and must be available when validating
    # typed references; otherwise FACT-POST/INF-POST tokens are incorrectly
    # stripped as unresolved.  No rows are created here: only existing ledger
    # rows are combined into the known-reference sets.
    fact_rows = _ledger_rows(jsonl_blocks, "fact_ledger")
    inference_rows = _ledger_rows(jsonl_blocks, "inference_ledger")
    source_ids = _known_ids(source_ledger_rows, "source_id")
    source_rows_by_id = _source_rows_by_id(source_ledger_rows)
    fact_ids = _known_ids(fact_rows, "fact_id")
    inference_ids = _known_ids(inference_rows, "inference_id")
    fact_source_ids_by_id = _fact_source_ids_by_id(fact_rows, source_ids)
    inference_fact_ids_by_id = _inference_fact_ids_by_id(inference_rows, fact_ids)

    available_from = _resolve_available_from(
        front,
        old_manifest,
        episode,
        trade_date=trade_date,
    )

    # Ticker-only joins can attach another event's evidence to the wrong record.
    # Repair therefore starts from each record's own explicit fields/payload and
    # downgrades unresolved training rows instead of guessing provenance.
    old_records = deepcopy(jsonl_blocks.get("brain_delta.jsonl", []))
    _normalize_null_event_references(old_records)
    _normalize_mistyped_related_event_references(
        old_records,
        json_blocks=json_blocks,
        jsonl_blocks=jsonl_blocks,
    )
    _materialize_case_population_ids(old_records, jsonl_blocks)
    # Preserve every source brain_delta record. Unresolved provenance or an
    # outcome-only row is retained for audit/context but downgraded from
    # training instead of being dropped or supplied with invented evidence.
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
    _normalize_numeric_identifier_fields(repaired_records)
    outcome_only_excluded_record_count = _exclude_outcome_only_training_records(
        repaired_records,
        source_rows_by_id=source_rows_by_id,
    )
    repaired_original_records = list(repaired_records)
    _materialize_missing_explicit_case_records(
        repaired_records,
        jsonl_blocks=jsonl_blocks,
        episode_id=episode_id,
        trade_date=trade_date,
        available_from=available_from,
        known_fact_ids=fact_ids,
        known_inference_ids=inference_ids,
    )
    record_id_map = _record_id_map(old_records, repaired_original_records)
    _rewrite_cross_record_references(json_blocks, record_id_map)
    _rewrite_cross_record_references(jsonl_blocks, record_id_map)
    jsonl_blocks["brain_delta.jsonl"] = repaired_records
    if "record_provenance_closure_audit.jsonl" in jsonl_blocks:
        jsonl_blocks["record_provenance_closure_audit.jsonl"] = _repair_provenance_closure_rows(
            jsonl_blocks["record_provenance_closure_audit.jsonl"],
            repaired_records,
            fact_source_ids_by_id=fact_source_ids_by_id,
            inference_fact_ids_by_id=inference_fact_ids_by_id,
        )
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

    candidate_semantic_rows = _materialize_missing_candidate_semantic_witness_rows(
        jsonl_blocks.get("candidate_semantic_witness.jsonl", []),
        screening_rows=jsonl_blocks.get("candidate_screening.jsonl", []),
        ranking_rows=jsonl_blocks.get("candidate_ranking_audit.jsonl", []),
        fact_rows=jsonl_blocks.get("fact_ledger_blind.jsonl", []),
        inference_rows=jsonl_blocks.get("inference_ledger_blind.jsonl", []),
        material_review_rows=jsonl_blocks.get("material_review.jsonl", []),
        source_rows=source_ledger_rows,
    )
    candidate_semantic_rows, final_witness_rows = _repair_semantic_primary_fact_references(
        candidate_semantic_rows,
        jsonl_blocks.get("final_evidence_witness.jsonl", []),
        screening_rows=jsonl_blocks.get("candidate_screening.jsonl", []),
        fact_rows=fact_rows,
    )
    if candidate_semantic_rows:
        jsonl_blocks["candidate_semantic_witness.jsonl"] = candidate_semantic_rows
    if final_witness_rows:
        jsonl_blocks["final_evidence_witness.jsonl"] = final_witness_rows
    if "candidate_semantic_witness.jsonl" in jsonl_blocks:
        jsonl_blocks["candidate_semantic_witness.jsonl"] = (
            _repair_candidate_semantic_alias_rows(
                jsonl_blocks["candidate_semantic_witness.jsonl"],
                entity_resolution_rows=jsonl_blocks.get("entity_resolution.jsonl", []),
                final_witness_rows=jsonl_blocks.get("final_evidence_witness.jsonl", []),
            )
        )
    if "final_semantic_audit.jsonl" in jsonl_blocks:
        jsonl_blocks["final_semantic_audit.jsonl"] = [
            _repair_semantic_audit_row(row) for row in jsonl_blocks["final_semantic_audit.jsonl"]
        ]

    semantic_relation_ids = semantic_exclusion_relation_ids(jsonl_blocks)
    semantic_excluded_record_count = _exclude_semantically_invalid_training_records(
        repaired_records,
        semantic_relation_ids=semantic_relation_ids,
    )
    semantic_excluded_record_count += _exclude_unverifiable_outcome_training_records(
        repaired_records,
    )
    _normalize_issuer_day_weights(
        [record for record in repaired_records if record.get("record_type") == "supervised_issuer_day_case"]
    )
    _normalize_issuer_day_weights(
        [record for record in repaired_records if record.get("record_type") == "supervised_direct_event_case"]
    )
    _normalize_ineligible_training_metadata(repaired_records)
    if "record_provenance_closure_audit.jsonl" in jsonl_blocks:
        jsonl_blocks["record_provenance_closure_audit.jsonl"] = _repair_provenance_closure_rows(
            jsonl_blocks["record_provenance_closure_audit.jsonl"],
            repaired_records,
            fact_source_ids_by_id=fact_source_ids_by_id,
            inference_fact_ids_by_id=inference_fact_ids_by_id,
        )

    sample_weight_summary = _sample_weight_summary(repaired_records)
    training_count = sum(1 for record in repaired_records if record.get("training_eligible") is True)

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
        quarantine_status=quarantine_status,
    )
    json_blocks["validation_report.json"] = _validation_report(
        old_validation,
        episode_id=episode_id,
        record_count=len(repaired_records),
        training_count=training_count,
        sample_weight_summary=sample_weight_summary,
        quarantine_status=quarantine_status,
    )
    json_blocks["direct_ingest_contract.json"] = _direct_ingest_contract(
        episode_id=episode_id,
        record_count=len(repaired_records),
        training_count=training_count,
        sample_weight_summary=sample_weight_summary,
        quarantine_status=quarantine_status,
    )

    front = _repair_front_matter(
        front,
        episode_id=episode_id,
        trade_date=trade_date,
        available_from=available_from,
        record_count=len(repaired_records),
        training_count=training_count,
        quarantine_status=quarantine_status,
    )

    # Recompute contracts and hashes from the final repaired payload, never
    # from source-declared counts that may describe a different wrapper shape.
    block_payloads = _block_payloads(parsed.blocks, json_blocks, jsonl_blocks)
    json_blocks["bundle_manifest.json"] = _bundle_manifest(
        old_manifest,
        episode_id=episode_id,
        created_at=_first_string(
            front.get("created_at"),
            old_manifest.get("created_at"),
            episode.get("created_at"),
            available_from,
        ),
        record_count=len(repaired_records),
        training_count=training_count,
        block_payloads=block_payloads,
        quarantine_status=quarantine_status,
    )
    block_payloads["bundle_manifest.json"] = _json_payload(
        json_blocks["bundle_manifest.json"],
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_text = _render_bundle(front, block_payloads)
    _atomic_write_text(output_path, output_text)

    return {
        "input": str(input_path),
        "output": str(output_path),
        "episode_id": episode_id,
        "episode_id_derived": episode_id_derived,
        "trade_date": trade_date,
        "record_count": len(repaired_records),
        "training_eligible_record_count": training_count,
        "semantic_excluded_record_count": semantic_excluded_record_count,
        "outcome_only_excluded_record_count": outcome_only_excluded_record_count,
        "record_counts_by_type": dict(Counter(row["record_type"] for row in repaired_records)),
        "final_semantic_audit_rows": len(jsonl_blocks.get("final_semantic_audit.jsonl", [])),
        "sample_weight_validation_status": sample_weight_summary["status"],
        "source_timestamp_repair": timestamp_repair_summary,
        "source_reference_filter": {
            "known_source_count": len(source_ids),
            "known_fact_count": len(fact_ids),
            "known_inference_count": len(inference_ids),
        },
    }


_CASE_POPULATION_REPAIR_SPECS: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    (
        "issuer_day_cases.jsonl",
        "supervised_issuer_day_case",
        ("issuer_day_case_id", "case_id"),
        "issuer_day_case_id",
    ),
    (
        "direct_event_cases.jsonl",
        "supervised_direct_event_case",
        ("direct_event_case_id", "case_id"),
        "direct_event_case_id",
    ),
    (
        "blind_leader_preference_pairs.jsonl",
        "blind_leader_preference_pair",
        ("pair_id", "sealed_pair_id", "case_id"),
        "pair_id",
    ),
    (
        "candidate_generation_error_cases.jsonl",
        "candidate_generation_error_case",
        ("candidate_generation_error_case_id", "case_id"),
        "candidate_generation_error_case_id",
    ),
    (
        "ranking_error_cases.jsonl",
        "ranking_error_case",
        ("ranking_error_case_id", "case_id"),
        "ranking_error_case_id",
    ),
    (
        "newsless_or_unexplained_cases.jsonl",
        "newsless_or_unexplained_case",
        ("newsless_case_id", "case_id"),
        "newsless_case_id",
    ),
    (
        "beneficiary_discovery_cases.jsonl",
        "beneficiary_discovery_case",
        ("beneficiary_case_id", "beneficiary_discovery_case_id", "case_id"),
        "beneficiary_case_id",
    ),
    (
        "negative_control_cases.jsonl",
        "negative_control_case",
        ("negative_control_id", "negative_control_case_id", "case_id"),
        "negative_control_id",
    ),
    (
        "theme_formation_cases.jsonl",
        "theme_formation_case",
        ("theme_case_id", "theme_formation_case_id", "case_id"),
        "theme_case_id",
    ),
    (
        "context_market_state_or_fact_cases.jsonl",
        "context_market_state_or_fact_case",
        ("context_case_id", "context_market_state_or_fact_case_id", "case_id"),
        "context_case_id",
    ),
)

# These artifacts can be complete first-class records even when a legacy
# writer omitted their brain_delta mirrors. Other case lanes already have
# aggregate/split representations whose coverage is proven by relation joins;
# duplicating those rows would change their training population.
_EXPLICIT_CASE_RECORD_MATERIALIZATION_BLOCKS = {
    "beneficiary_discovery_cases.jsonl",
    "blind_leader_preference_pairs.jsonl",
    "candidate_generation_error_cases.jsonl",
    "newsless_or_unexplained_cases.jsonl",
    "ranking_error_cases.jsonl",
    "theme_formation_cases.jsonl",
    "context_market_state_or_fact_cases.jsonl",
}


def _materialize_case_population_ids(
    records: list[dict[str, Any]],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> None:
    """Attach case IDs only when source case and brain rows join uniquely."""

    for block_name, record_type, id_fields, target_field in _CASE_POPULATION_REPAIR_SPECS:
        proposals: list[tuple[str, dict[str, Any], dict[str, Any], dict[str, Any]]] = []
        for case in jsonl_blocks.get(block_name, []):
            case_id = _first_string(*(case.get(field) for field in id_fields))
            if case_id is None:
                continue
            if block_name == "issuer_day_cases.jsonl":
                # One issuer-day aggregate may be represented by several
                # direct-event records, one per sealed fact.  If no canonical
                # issuer-day row exists, retain the aggregate ID on every
                # record whose explicit fact/ticker/date evidence intersects.
                canonical_matches = [
                    record
                    for record in records
                    if record.get("record_type") == record_type
                    and _case_population_join_evidence(case, record) is not None
                ]
                if not canonical_matches:
                    for record in records:
                        if record.get("record_type") != "supervised_direct_event_case":
                            continue
                        evidence = _case_population_join_evidence(
                            case,
                            record,
                            allow_partial_case_facts=True,
                        )
                        if evidence is not None:
                            proposals.append((case_id, case, record, evidence))
                    continue
            matches: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
            allowed_record_types = _case_population_record_types(
                block_name,
                case,
                canonical_record_type=record_type,
            )
            for record in records:
                if record.get("record_type") not in allowed_record_types:
                    continue
                evidence = _case_population_join_evidence(
                    case,
                    record,
                    allow_partial_case_facts=block_name == "ranking_error_cases.jsonl",
                )
                if evidence is None:
                    continue
                preferred_record_types = {record_type}
                if block_name == "ranking_error_cases.jsonl":
                    # Legacy bundles use the more specific
                    # ``candidate_ranking_error_case`` type for this artifact.
                    # Prefer that explicit error record over an issuer/direct
                    # mirror when all evidence joins are otherwise identical.
                    preferred_record_types.add("candidate_ranking_error_case")
                score = int(record.get("record_type") in preferred_record_types) * 100
                score += int(evidence["classification_matches"]) * 30
                score += int("outcome_leader_id" in evidence["join_values"]) * 20
                score += int("outcome_audit_id" in evidence["join_values"]) * 10
                score += int("candidate_id" in evidence["join_values"]) * 8
                score += int("source_screening_id" in evidence["join_values"]) * 6
                score += int("theme_id" in evidence["join_values"]) * 12
                score += int("fact_id" in evidence["join_values"]) * 5
                score += int(evidence["fact_ids_match"]) * 5
                score += int(evidence["fact_ids_exact"]) * 20
                # Set equality cannot distinguish a directed pair from its
                # reverse. Preserve the source ordering when both sides expose
                # it so only the exact pair orientation receives this boost.
                score += int(evidence["ordered_fact_ids_exact"]) * 40
                score += int(evidence["ticker_matches"]) * 2
                matches.append((score, record, evidence))
            if not matches:
                continue
            best_score = max(score for score, _, _ in matches)
            best_matches = [item for item in matches if item[0] == best_score]
            if len(best_matches) != 1:
                continue
            _, record, evidence = best_matches[0]
            if record.get(target_field) not in {None, case_id}:
                continue
            proposals.append((case_id, case, record, evidence))

        proposal_counts = Counter((id(record), target_field) for _, _, record, _ in proposals)
        for case_id, case, record, evidence in proposals:
            if proposal_counts[(id(record), target_field)] != 1:
                continue
            existing_derivations = record.get("repair_population_derivations")
            if existing_derivations is not None and not isinstance(existing_derivations, list):
                continue
            record[target_field] = case_id
            derivations = record.setdefault("repair_population_derivations", [])
            assert isinstance(derivations, list)
            derivation = {
                "rule_id": "case_id_from_unique_evidence_join.v2",
                "source_artifact": block_name,
                "source_case_id": case_id,
                "source_case_payload_sha256": sha256_text(canonical_json(case)),
                "target_field": target_field,
                "join_field": evidence["primary_join_field"],
                "join_value": evidence["primary_join_value"],
                "join_values": evidence["join_values"],
                "record_type_relation": (
                    f"{block_name}:{case.get('classification')}->{record.get('record_type')}"
                ),
            }
            if derivation not in derivations:
                derivations.append(derivation)


def _materialize_missing_material_review_rows(
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> None:
    """Normalize queue-embedded reviews without inventing review content.

    A legacy run can emit a complete ``material_review_queue`` for every
    source row while a separate ``material_review`` block contains only a
    subset.  The queue row already has the reviewed decision, quote, and
    source identity, so the missing canonical rows can be represented by a
    deterministic copy.  This is an artifact-shape repair only: no new
    material judgment, issuer, fact, or inference is created.
    """

    queue_rows = jsonl_blocks.get("material_review_queue.jsonl", [])
    if not queue_rows:
        return
    review_rows = jsonl_blocks.setdefault("material_review.jsonl", [])
    reviewed_source_ids = {
        source_id
        for row in review_rows
        for source_id in [
            _first_string(
                row.get("source_id"),
                row.get("source_row_id"),
                row.get("row_id"),
            )
        ]
        if source_id is not None
    }
    # Some legacy bundles use different source-id namespaces for the queue
    # row (NEWS-...) and the existing review (SRC-...).  The queue foreign key
    # is the stable identity in that shape; without checking it we would
    # append a second review for every already-covered queue and trip the
    # material_review_queue_id uniqueness gate.
    reviewed_queue_ids = {
        queue_id
        for row in review_rows
        for queue_id in [
            _first_string(
                row.get("material_review_queue_id"),
                row.get("material_queue_id"),
                row.get("queue_id"),
            )
        ]
        if queue_id is not None
    }
    existing_review_ids = {
        review_id
        for row in review_rows
        for review_id in [
            _first_string(
                row.get("material_review_id"),
                row.get("review_id"),
            )
        ]
        if review_id is not None
    }
    for queue_row in queue_rows:
        source_id = _first_string(
            queue_row.get("source_row_id"),
            queue_row.get("source_id"),
            queue_row.get("row_id"),
        )
        queue_review_id = _first_string(
            queue_row.get("material_review_id"),
            queue_row.get("review_id"),
        )
        if source_id is None or queue_review_id is None:
            continue
        queue_id = _first_string(
            queue_row.get("material_review_queue_id"),
            queue_row.get("material_queue_id"),
            queue_row.get("queue_id"),
        )
        if queue_id is not None and queue_id in reviewed_queue_ids:
            continue
        if source_id in reviewed_source_ids:
            continue
        derived_review_id = queue_review_id
        if derived_review_id in existing_review_ids:
            # A legacy alias can reuse the same numeric review suffix for a
            # different source row. Keep both rows and make the derived key
            # deterministic rather than silently dropping the queue evidence.
            suffix = re.search(r"(\d+)$", source_id)
            suffix_text = suffix.group(1) if suffix is not None else sha256_text(source_id)[:12]
            derived_review_id = f"{queue_review_id}__QUEUE-{suffix_text}"
        derived = deepcopy(queue_row)
        derived["material_review_id"] = derived_review_id
        derived["material_queue_id"] = _first_string(
            queue_row.get("material_queue_id"),
            queue_row.get("material_review_queue_id"),
            queue_row.get("queue_id"),
            derived_review_id,
        )
        derived["source_id"] = source_id
        derived["source_row_id"] = source_id
        derived["row_id"] = _first_string(queue_row.get("row_id"), source_id)
        derived["review_method"] = "DERIVED_FROM_MATERIAL_REVIEW_QUEUE"
        derived["repair_derived_from_queue"] = True
        derived["repair_population_derivation"] = {
            "rule_id": "material_review_from_explicit_queue_row.v1",
            "source_artifact": "material_review_queue.jsonl",
            "source_queue_review_id": queue_review_id,
            "source_row_id": source_id,
            "source_row_payload_sha256": sha256_text(canonical_json(queue_row)),
        }
        review_rows.append(derived)
        reviewed_source_ids.add(source_id)
        existing_review_ids.add(derived_review_id)
        if queue_id is not None:
            reviewed_queue_ids.add(queue_id)


def _normalize_outcome_identity_aliases(
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> None:
    """Close legacy outcome/leader ID spellings using unique ticker evidence."""

    outcome_ids_by_ticker: dict[str, set[str]] = {}
    for row in jsonl_blocks.get("outcome_ledger.jsonl", []):
        ticker = _first_string(row.get("ticker"), row.get("code"))
        outcome_id = _first_string(row.get("outcome_ledger_id"), row.get("outcome_id"))
        if ticker is not None and outcome_id is not None:
            outcome_ids_by_ticker.setdefault(ticker, set()).add(outcome_id)
    canonical_outcome_by_ticker = {
        ticker: next(iter(ids))
        for ticker, ids in outcome_ids_by_ticker.items()
        if len(ids) == 1
    }

    for row in jsonl_blocks.get("outcome_leader_census.jsonl", []):
        ticker = _first_string(row.get("ticker"), row.get("code"))
        canonical_outcome = canonical_outcome_by_ticker.get(ticker or "")
        if canonical_outcome is None:
            continue
        for field in ("outcome_id", "outcome_ledger_id"):
            if field in row and isinstance(row.get(field), str):
                row[field] = canonical_outcome

    leader_ids_by_ticker: dict[str, set[str]] = {}
    for row in jsonl_blocks.get("outcome_leader_census.jsonl", []):
        ticker = _first_string(row.get("ticker"), row.get("code"))
        leader_id = _first_string(row.get("outcome_leader_id"), row.get("leader_id"))
        if ticker is not None and leader_id is not None:
            leader_ids_by_ticker.setdefault(ticker, set()).add(leader_id)
    canonical_leader_by_ticker = {
        ticker: next(iter(ids))
        for ticker, ids in leader_ids_by_ticker.items()
        if len(ids) == 1
    }
    for row in jsonl_blocks.get("outcome_to_news_audit.jsonl", []):
        ticker = _first_string(row.get("ticker"), row.get("code"))
        canonical_leader = canonical_leader_by_ticker.get(ticker or "")
        if canonical_leader is None:
            continue
        for field in ("outcome_leader_id", "leader_id", "leader_census_id"):
            if field in row and isinstance(row.get(field), str):
                # These fields are not interchangeable when a legacy audit
                # carries both namespaces (for example LEAD-* and OL-*).
                # Preserve the explicit auxiliary leader_id/census ID and
                # canonicalize only the outcome_leader_id alias.
                if (
                    field in {"leader_id", "leader_census_id"}
                    and isinstance(row.get("outcome_leader_id"), str)
                    and row.get(field) != row.get("outcome_leader_id")
                ):
                    continue
                row[field] = canonical_leader


def _case_population_record_types(
    block_name: str,
    case: dict[str, Any],
    *,
    canonical_record_type: str,
) -> set[str]:
    if block_name == "issuer_day_cases.jsonl":
        # Some bundles store one issuer-day aggregate as several explicit
        # direct-event records (one per sealed fact).  The aggregate may be
        # attached only when the unique fact/ticker/date join proves it.
        return {canonical_record_type, "supervised_direct_event_case"}
    if block_name == "theme_formation_cases.jsonl":
        return {canonical_record_type, "supervised_theme_formation_case"}
    if block_name == "negative_control_cases.jsonl":
        # Preserve a source negative-control ID on an existing exact
        # direct-event/issuer-day row when a dedicated row is absent. This is
        # an evidence-backed alias, not a fabricated semantic record.
        return {
            canonical_record_type,
            "row_disposition_error_case",
            "supervised_direct_event_case",
            "supervised_issuer_day_case",
        }
    if block_name == "ranking_error_cases.jsonl":
        return {
            canonical_record_type,
            "candidate_ranking_error_case",
            "supervised_issuer_day_case",
            "supervised_direct_event_case",
        }
    if block_name != "beneficiary_discovery_cases.jsonl":
        return {canonical_record_type}
    classification = str(
        case.get("classification")
        or case.get("discovery_type")
        or case.get("discovery_classification")
        or case.get("screening_or_generation_failure")
        or ""
    ).upper()
    aliases = {
        "CANDIDATE_GENERATION_MISS": {"candidate_generation_error_case"},
        "RANKING_MISS": {"candidate_ranking_error_case", "ranking_error_case"},
        "SCREENED_OUT_BUT_WINNER": {
            "candidate_ranking_error_case",
            "ranking_error_case",
        },
        "SEMANTIC_FALSE_POSITIVE": {
            "entity_resolution_error_case",
            "negative_control_case",
        },
        "SEALED_THEME_MEMBER_NOT_GENERATED": {"candidate_generation_error_case"},
    }
    return {canonical_record_type, *aliases.get(classification, set())}


def _case_population_join_evidence(
    case: dict[str, Any],
    record: dict[str, Any],
    *,
    allow_partial_case_facts: bool = False,
) -> dict[str, Any] | None:
    case_leaders = _population_relation_values(case, "outcome_leader_id")
    record_leaders = _population_relation_values(record, "outcome_leader_id")
    case_candidates = _population_relation_values(case, "candidate_id", "candidate_ids")
    record_candidates = _population_relation_values(record, "candidate_id", "candidate_ids")
    case_themes = _population_relation_values(
        case,
        "theme_id",
        "theme_ids",
        "theme",
        "theme_key",
        "theme_keys",
    )
    record_themes = _population_relation_values(
        record,
        "theme_id",
        "theme_ids",
        "theme",
        "theme_key",
        "theme_keys",
    )
    case_screenings = _population_relation_values(
        case,
        "source_screening_id",
        "source_screening_ids",
        "screening_id",
        "screening_ids",
    )
    record_screenings = _population_relation_values(
        record,
        "source_screening_id",
        "source_screening_ids",
        "screening_id",
        "screening_ids",
    )
    case_audits = _population_relation_values(
        case,
        "audit_id",
        "audit_ids",
        "outcome_audit_id",
        "outcome_audit_ids",
    )
    record_audits = _population_relation_values(
        record,
        "audit_id",
        "audit_ids",
        "outcome_audit_id",
        "outcome_audit_ids",
    )
    case_fact_ids = _population_relation_values(
        case,
        "matched_fact_ids",
        "sealed_fact_ids",
        "sealed_source_fact_ids",
        "source_fact_ids",
        "combined_fact_ids",
        "fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    )
    record_fact_ids = _population_relation_values(
        record,
        "source_fact_ids",
        "fact_ids",
        "blind_fact_ids",
        "sealed_fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    )
    case_fact_sequence = _population_relation_sequence(
        case,
        "matched_fact_ids",
        "sealed_fact_ids",
        "sealed_source_fact_ids",
        "source_fact_ids",
        "combined_fact_ids",
        "fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    )
    record_fact_sequence = _population_relation_sequence(
        record,
        "source_fact_ids",
        "fact_ids",
        "blind_fact_ids",
        "sealed_fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    )
    # Legacy bundles occasionally vary only the zero-padding of an opaque
    # relation ID (for example OUTNEWS-0004 vs OUTNEWS-000004).  Compare a
    # normalized alias key, but return the source-side value so the repair
    # remains an explicit, traceable ID join rather than a fabricated ID.
    shared_leaders = _shared_relation_values(case_leaders, record_leaders)
    shared_candidates = _shared_relation_values(case_candidates, record_candidates)
    shared_themes = _shared_relation_values(case_themes, record_themes)
    shared_screenings = _shared_relation_values(case_screenings, record_screenings)
    shared_audits = _shared_relation_values(case_audits, record_audits)
    shared_facts = _shared_relation_values(case_fact_ids, record_fact_ids)
    case_ticker = _first_string(case.get("ticker"), case.get("candidate_ticker"))
    record_ticker = _first_string(record.get("ticker"), record.get("candidate_ticker"))
    ticker_matches = bool(case_ticker and record_ticker and case_ticker == record_ticker)
    case_trade_date = _first_string(case.get("trade_date"))
    record_trade_date = _first_string(record.get("trade_date"))
    trade_date_matches = bool(
        case_trade_date and record_trade_date and case_trade_date == record_trade_date
    )
    if (
        not shared_leaders
        and not shared_audits
        and not shared_candidates
        and not shared_themes
        and not shared_screenings
        and not shared_facts
        and not ticker_matches
    ):
        return None
    if case_leaders and record_leaders and not shared_leaders:
        return None
    if case_candidates and record_candidates and not shared_candidates:
        return None
    if case_themes and record_themes and not shared_themes:
        return None
    if case_screenings and record_screenings and not shared_screenings:
        return None
    if case_audits and record_audits and not shared_audits:
        return None
    if case_fact_ids and record_fact_ids and not shared_facts:
        return None

    if case_ticker and record_ticker and case_ticker != record_ticker:
        return None
    if case_trade_date and record_trade_date and case_trade_date != record_trade_date:
        return None

    if (
        case_fact_ids
        and not case_fact_ids.issubset(record_fact_ids)
        and not (allow_partial_case_facts and shared_facts)
    ):
        return None

    case_classification = _first_string(case.get("classification"))
    record_classifications = _population_relation_values(
        record,
        "classification",
        "postmortem_class",
    )
    classification_matches = bool(
        case_classification
        and case_classification in record_classifications
    )
    if record_classifications and case_classification and not classification_matches:
        return None

    join_values: dict[str, list[str]] = {}
    if shared_leaders:
        join_values["outcome_leader_id"] = shared_leaders
    if shared_audits:
        join_values["outcome_audit_id"] = shared_audits
    if shared_candidates:
        join_values["candidate_id"] = shared_candidates
    if shared_themes:
        join_values["theme_id"] = shared_themes
    if shared_screenings:
        join_values["source_screening_id"] = shared_screenings
    if shared_facts:
        join_values["fact_id"] = shared_facts
    has_explicit_join = bool(
        shared_leaders
        or shared_audits
        or shared_candidates
        or shared_themes
        or shared_screenings
        or shared_facts
    )
    if ticker_matches and not has_explicit_join and case_ticker is not None:
        join_values["ticker"] = [case_ticker]
    if trade_date_matches and not has_explicit_join and case_trade_date is not None:
        join_values["trade_date"] = [case_trade_date]
    primary_join_field = (
        "outcome_leader_id"
        if shared_leaders
        else "outcome_audit_id"
        if shared_audits
        else "candidate_id"
        if shared_candidates
        else "theme_id"
        if shared_themes
        else "source_screening_id"
        if shared_screenings
        else "fact_id"
        if shared_facts
        else "ticker"
        if ticker_matches
        else "source_screening_id"
    )
    return {
        "classification_matches": classification_matches,
        "fact_ids_match": bool(shared_facts),
        "fact_ids_exact": bool(
            case_fact_ids and record_fact_ids and case_fact_ids == record_fact_ids
        ),
        "ordered_fact_ids_exact": bool(
            case_fact_sequence
            and record_fact_sequence
            and case_fact_sequence == record_fact_sequence
        ),
        "ticker_matches": bool(case_ticker and record_ticker),
        "join_values": join_values,
        "primary_join_field": primary_join_field,
        "primary_join_value": join_values[primary_join_field][0],
    }


def _shared_relation_values(left: set[str], right: set[str]) -> list[str]:
    """Return left-side IDs with a conservative zero-padding alias join."""

    right_by_key: dict[str, str] = {}
    for value in right:
        right_by_key.setdefault(_relation_alias_key(value), value)
    return sorted(
        value
        for value in left
        if _relation_alias_key(value) in right_by_key
    )


def _relation_alias_key(value: str) -> str:
    # Only normalize numeric runs after an alphabetic/punctuation prefix.  The
    # original value is retained in all output; this key is for matching
    # legacy IDs, not for rewriting user data.
    return re.sub(r"\d+", lambda match: str(int(match.group(0))), value)


def _population_relation_values(
    row: dict[str, Any],
    *fields: str,
) -> set[str]:
    values: set[str] = set()
    payload = row.get("payload")
    containers = [row, payload] if isinstance(payload, dict) else [row]
    for container in containers:
        for field in fields:
            value = container.get(field)
            if isinstance(value, str) and value:
                values.add(value)
            elif isinstance(value, list):
                values.update(item for item in value if isinstance(item, str) and item)
    return values


def _population_relation_sequence(
    row: dict[str, Any],
    *fields: str,
) -> list[str]:
    """Return the first declared relation sequence without losing direction."""

    payload = row.get("payload")
    containers = [row, payload] if isinstance(payload, dict) else [row]
    for container in containers:
        for field in fields:
            value = container.get(field)
            if isinstance(value, str) and value:
                return [value]
            if isinstance(value, list):
                items = [item for item in value if isinstance(item, str) and item]
                if items:
                    return items
    return []


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
    record_id_map: dict[str, str] = {}
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
        _drop_training_without_provenance(record)
        _drop_unsealed_preference_pair(record)
        old_record_id = _first_string(record.get("record_id"), record.get("brain_delta_id"))
        _namespace_record_identity(record, episode_id=episode_id)
        new_record_id = _first_string(record.get("record_id"), record.get("brain_delta_id"))
        if old_record_id is not None and new_record_id is not None:
            record_id_map[old_record_id] = new_record_id
        if record.get("record_type") == "supervised_issuer_day_case":
            issuer_day_records.append(record)
        if record.get("record_type") == "supervised_direct_event_case":
            direct_event_records.append(record)
        repaired.append(record)
    for record in repaired:
        _rewrite_cross_record_references(record, record_id_map)
    _normalize_issuer_day_weights(issuer_day_records)
    _normalize_issuer_day_weights(direct_event_records)
    return repaired


def _materialize_missing_explicit_case_records(
    repaired_records: list[dict[str, Any]],
    *,
    jsonl_blocks: dict[str, list[dict[str, Any]]],
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    known_fact_ids: set[str],
    known_inference_ids: set[str],
) -> None:
    """Materialize explicit case artifacts omitted from ``brain_delta``.

    A case artifact is a first-class source record. This adapter copies every
    source field and adds only a deterministic envelope, canonical identity,
    weight, and lineage receipt. Undefined typed references are retained as
    legacy tokens instead of being exposed as live importer references. It
    never infers a company, ticker, outcome, eligibility, or evidence value.
    """

    for block_name, record_type, id_fields, target_field in _CASE_POPULATION_REPAIR_SPECS:
        if block_name not in _EXPLICIT_CASE_RECORD_MATERIALIZATION_BLOCKS:
            continue
        existing_case_ids: set[str] = set()
        for record in repaired_records:
            existing_case_ids.update(
                value
                for field in (*id_fields, target_field)
                for value in _string_list(record.get(field))
            )
            derivations = record.get("repair_population_derivations")
            if isinstance(derivations, list):
                existing_case_ids.update(
                    source_case_id
                    for item in derivations
                    if isinstance(item, dict)
                    and item.get("source_artifact") == block_name
                    for source_case_id in [_first_string(item.get("source_case_id"))]
                    if source_case_id is not None
                )

        for case in jsonl_blocks.get(block_name, []):
            case_id = _first_string(*(case.get(field) for field in id_fields))
            if case_id is None or case_id in existing_case_ids:
                continue
            if not isinstance(case.get("training_eligible"), bool):
                # Source ambiguity is not permission to invent either a
                # positive or a negative training decision.
                continue
            if case.get("training_eligible") is False and _first_string(
                case.get("training_exclusion_reason"),
                case.get("eligibility_reason"),
                case.get("no_direct_bridge_reason"),
                case.get("exclusion_reason"),
                case.get("case_status"),
                case.get("classification"),
            ) is None:
                continue

            case_sha256 = sha256_text(canonical_json(case))
            derived = deepcopy(case)
            derived_record_id = f"DERIVED-CASE-{case_sha256[:16].upper()}"
            declared_weight = _float_or_none(case.get("sample_weight"))
            case_source_ids = _ordered_unique(
                [
                    *_string_list(case.get("provenance_source_ids")),
                    *_string_list(case.get("source_ids")),
                    *_string_list(case.get("matched_source_row_ids")),
                    *_string_list(case.get("matched_source_ids")),
                ]
            )
            derived.update(
                {
                    "record_id": derived_record_id,
                    "brain_delta_id": derived_record_id,
                    "record_type": record_type,
                    target_field: case_id,
                    "episode_id": episode_id,
                    "trade_date": _first_string(case.get("trade_date"), trade_date),
                    "available_from": _valid_available_from(
                        case.get("available_from"),
                        available_from,
                    ),
                    "sample_weight": (
                        declared_weight
                        if declared_weight is not None
                        else 1.0
                        if case.get("training_eligible") is True
                        else 0.0
                    ),
                    "repair_population_derivations": [
                        {
                            "rule_id": (
                                "derived_brain_record_from_explicit_case_artifact.v1"
                            ),
                            "source_artifact": block_name,
                            "source_case_id": case_id,
                            "source_case_payload_sha256": case_sha256,
                            "target_field": target_field,
                            "record_type_relation": (
                                f"{block_name}:explicit_case->{record_type}"
                            ),
                            "derivation_inputs": _ordered_unique(
                                [
                                    case_id,
                                    *_string_list(case.get("source_fact_ids")),
                                    *_string_list(case.get("fact_ids")),
                                    *_string_list(case.get("source_inference_ids")),
                                    *_string_list(case.get("inference_ids")),
                                    *case_source_ids,
                                ]
                            ),
                        }
                    ],
                }
            )
            if case_source_ids:
                # Legacy error cases call their sealed provenance edge
                # ``matched_source_row_ids``. Mirror that exact declared set
                # into the importer field without changing the source case.
                derived["provenance_source_ids"] = case_source_ids
            unresolved_fact_ids, unresolved_inference_ids = (
                _sanitize_unknown_typed_references(
                    derived,
                    known_fact_ids=known_fact_ids,
                    known_inference_ids=known_inference_ids,
                )
            )
            if unresolved_fact_ids:
                derived["legacy_unresolved_fact_tokens"] = _ordered_unique(
                    unresolved_fact_ids
                )
            if unresolved_inference_ids:
                derived["legacy_unresolved_inference_tokens"] = _ordered_unique(
                    unresolved_inference_ids
                )
            if unresolved_fact_ids or unresolved_inference_ids:
                derived["unresolved_reference_reason"] = (
                    "typed_reference_not_present_in_bundle_ledger"
                )
            # An explicit pair can be preserved as a record, but it is not a
            # training example unless the source also proves the sealed pair
            # contract. This mirrors the guard applied to original records.
            _drop_unsealed_preference_pair(derived)
            _normalize_ineligible_training_metadata([derived])
            _namespace_record_identity(derived, episode_id=episode_id)
            repaired_records.append(derived)
            existing_case_ids.add(case_id)


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
    if record_id.casefold().startswith(prefix.casefold()):
        return f"{prefix}{record_id[len(prefix):]}"
    return f"{prefix}{record_id}"


def _rewrite_cross_record_references(
    value: Any,
    record_id_map: dict[str, str],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if (key in {"record_id", "brain_delta_id"} or key.endswith("_record_id")) and isinstance(item, str):
                value[key] = record_id_map.get(item, item)
            elif key.endswith("_record_ids") and isinstance(item, list):
                value[key] = [
                    record_id_map.get(candidate, candidate) if isinstance(candidate, str) else candidate
                    for candidate in item
                ]
            else:
                _rewrite_cross_record_references(item, record_id_map)
    elif isinstance(value, list):
        for item in value:
            _rewrite_cross_record_references(item, record_id_map)


def _record_id_map(
    source_records: list[dict[str, Any]],
    repaired_records: list[dict[str, Any]],
) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for source, repaired in zip(source_records, repaired_records, strict=True):
        source_id = _first_string(source.get("record_id"), source.get("brain_delta_id"))
        repaired_id = _first_string(
            repaired.get("record_id"),
            repaired.get("brain_delta_id"),
        )
        if source_id is not None and repaired_id is not None:
            mapping[source_id] = repaired_id
    return mapping


def _repair_provenance_closure_rows(
    rows: list[dict[str, Any]],
    repaired_records: list[dict[str, Any]],
    *,
    fact_source_ids_by_id: dict[str, list[str]] | None = None,
    inference_fact_ids_by_id: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    records_by_id: dict[str, dict[str, Any]] = {}
    for record in repaired_records:
        for field in (
            "record_id",
            "brain_delta_id",
            "brain_record_id",
            "legacy_record_id",
        ):
            record_id = _first_string(record.get(field))
            if record_id is not None:
                records_by_id.setdefault(record_id, record)
    repaired_rows: list[dict[str, Any]] = []
    emitted_record_ids: set[str] = set()
    for row in rows:
        repaired = dict(row)
        record_id = _first_string(
            repaired.get("record_id"),
            repaired.get("brain_delta_id"),
            repaired.get("brain_record_id"),
            repaired.get("legacy_record_id"),
        )
        if record_id is not None:
            emitted_record_ids.add(record_id)
        matched_record = records_by_id.get(record_id or "")
        if matched_record is None:
            repaired_rows.append(repaired)
            continue
        eligible = matched_record.get("training_eligible") is True
        source_ids = _string_list(matched_record.get("provenance_source_ids"))
        source_fact_ids = _string_list(matched_record.get("source_fact_ids"))
        source_inference_ids = _string_list(matched_record.get("source_inference_ids"))
        # Legacy closure rows sometimes copied only the direct fact list even
        # though the record also names an inference.  The inference ledger is
        # an explicit part of the record's evidence chain, so expand the
        # closure artifact from that ledger rather than weakening the audit to
        # accept an incomplete fact set.
        if inference_fact_ids_by_id:
            for inference_id in source_inference_ids:
                source_fact_ids = _merge_unique(
                    source_fact_ids,
                    inference_fact_ids_by_id.get(inference_id, []),
                )
        if fact_source_ids_by_id:
            for fact_id in source_fact_ids:
                source_ids = _merge_unique(
                    source_ids,
                    fact_source_ids_by_id.get(fact_id, []),
                )
        repaired.update(
            {
                "record_type": matched_record.get("record_type"),
                "resolved_provenance_source_ids": source_ids,
                "source_fact_ids": source_fact_ids,
                "source_inference_ids": source_inference_ids,
                "closure_status": (
                    "CLOSED"
                    if eligible
                    else "CLOSED_NOT_TRAINING"
                    if source_ids
                    else "NOT_TRAINING_NO_CLOSURE_REQUIRED"
                ),
                "training_eligible_after_closure": eligible,
                "sample_weight_after_closure": _float_or_none(matched_record.get("sample_weight")) or 0.0,
                "downgrade_reason": (
                    None if eligible else matched_record.get("training_exclusion_reason")
                ),
            }
        )
        repaired_rows.append(_compact(repaired))
    # A repair-only adapter may preserve an explicit case artifact as a new
    # brain record. Its closure row is derived from that same hashed case; do
    # not synthesize closure for ordinary source records whose original audit
    # row is genuinely missing.
    for record in repaired_records:
        record_id = _first_string(record.get("record_id"))
        derivations = record.get("repair_population_derivations")
        if (
            record_id is None
            or record_id in emitted_record_ids
            or not isinstance(derivations, list)
            or not any(
                isinstance(item, dict)
                and item.get("rule_id")
                == "derived_brain_record_from_explicit_case_artifact.v1"
                for item in derivations
            )
        ):
            continue
        source_ids = _string_list(record.get("provenance_source_ids"))
        source_fact_ids = _string_list(record.get("source_fact_ids"))
        source_inference_ids = _string_list(record.get("source_inference_ids"))
        if inference_fact_ids_by_id:
            for inference_id in source_inference_ids:
                source_fact_ids = _merge_unique(
                    source_fact_ids,
                    inference_fact_ids_by_id.get(inference_id, []),
                )
        if fact_source_ids_by_id:
            for fact_id in source_fact_ids:
                source_ids = _merge_unique(
                    source_ids,
                    fact_source_ids_by_id.get(fact_id, []),
                )
        eligible = record.get("training_eligible") is True
        repaired_rows.append(
            {
                "record_id": record_id,
                "record_type": record.get("record_type"),
                "resolved_provenance_source_ids": source_ids,
                "source_fact_ids": source_fact_ids,
                "source_inference_ids": source_inference_ids,
                "closure_status": (
                    "CLOSED"
                    if eligible
                    else "CLOSED_NOT_TRAINING"
                    if source_ids
                    else "NOT_TRAINING_NO_CLOSURE_REQUIRED"
                ),
                "training_eligible_after_closure": eligible,
                "sample_weight_after_closure": (
                    _float_or_none(record.get("sample_weight")) or 0.0
                ),
                "downgrade_reason": (
                    None if eligible else record.get("training_exclusion_reason")
                ),
                "repair_generated_for_derived_record": True,
            }
        )
        emitted_record_ids.add(record_id)
    return repaired_rows


def _drop_training_without_provenance(record: dict[str, Any]) -> None:
    if record.get("training_eligible") is not True:
        return
    if _string_list(record.get("provenance_source_ids")):
        return
    record["training_eligible"] = False
    record["sample_weight"] = 0.0
    record["training_exclusion_reason"] = "missing_provenance_source_ids"
    reason = _first_string(record.get("eligibility_reason"))
    suffix = "missing_provenance_source_ids"
    record["eligibility_reason"] = f"{reason}; {suffix}" if reason else suffix


def _normalize_ineligible_training_metadata(records: list[dict[str, Any]]) -> None:
    """Make every non-training record explicit for downstream brain lanes.

    Legacy bundles often carry ``training_eligible: false`` (or omit the
    field) without a reason.  The record remains unchanged as research
    evidence, but the importer/training contract needs an explicit zero
    weight and exclusion reason so it cannot be mistaken for a positive
    example later.
    """

    for record in records:
        if record.get("training_eligible") is True:
            continue
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        if _first_string(record.get("training_exclusion_reason")):
            continue
        reason = "source_declared_ineligible_without_reason"
        record["training_exclusion_reason"] = reason
        prior = _first_string(record.get("eligibility_reason"))
        record["eligibility_reason"] = f"{prior}; {reason}" if prior else reason


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
    return has_sealed_preference_pair(record)


def _reference_kind_for_field(field: str) -> str | None:
    """Return the ledger type represented by a reference-shaped field.

    Legacy postmortem rows use names such as ``postmortem_fact_id`` and
    ``source_inference_ids``.  They are still typed references for importer
    purposes, even when the producer used a non-canonical prefix.  Keeping
    this small key classifier local to repair avoids hard-coding any ticker or
    date while allowing unknown ledger references to be quarantined safely.
    """

    normalized = field.lower()
    for reference_type in ("fact", "inference"):
        if normalized == f"{reference_type}_id":
            return reference_type
        if normalized == f"{reference_type}_ids":
            return reference_type
        if normalized.endswith(f"_{reference_type}_id"):
            return reference_type
        if normalized.endswith(f"_{reference_type}_ids"):
            return reference_type
    return None


def _sanitize_unknown_typed_references(
    value: Any,
    *,
    known_fact_ids: set[str],
    known_inference_ids: set[str],
) -> tuple[list[str], list[str]]:
    """Remove only ledger references absent from this bundle's ledgers.

    The original token is returned to the caller for preservation in an
    explicit legacy field.  Payload prose and non-reference fields are left
    byte-for-byte equivalent; only keys whose names identify FACT/INF
    references are filtered.  This prevents the importer from accepting a
    fabricated placeholder while retaining the research artifact for audit.
    """

    unresolved_fact_ids: list[str] = []
    unresolved_inference_ids: list[str] = []

    def visit(container: Any) -> None:
        if isinstance(container, dict):
            for key in list(container):
                item = container[key]
                if key == "repair_population_derivations":
                    # Derivation metadata is audit evidence, not a live
                    # importer reference. Preserve legacy PMFACT/PMINF
                    # tokens used in its join proof exactly as supplied.
                    continue
                reference_type = _reference_kind_for_field(str(key))
                if reference_type is not None:
                    known = known_fact_ids if reference_type == "fact" else known_inference_ids
                    values = item if isinstance(item, list) else [item]
                    unknown = [
                        candidate
                        for candidate in values
                        if isinstance(candidate, str) and candidate and candidate not in known
                    ]
                    if unknown:
                        target = (
                            unresolved_fact_ids
                            if reference_type == "fact"
                            else unresolved_inference_ids
                        )
                        target.extend(unknown)
                    retained = _ordered_unique(
                        [candidate for candidate in values if isinstance(candidate, str) and candidate in known]
                    )
                    if isinstance(item, list):
                        if retained:
                            container[key] = retained
                        elif item == []:
                            # An explicitly empty typed-reference list means
                            # "no references". Preserve it byte-for-byte; it
                            # must not disappear merely because there is
                            # nothing to resolve.
                            continue
                        else:
                            container.pop(key, None)
                    elif item not in known:
                        container.pop(key, None)
                    continue
                visit(item)
        elif isinstance(container, list):
            for item in container:
                visit(item)

    visit(value)
    return _ordered_unique(unresolved_fact_ids), _ordered_unique(unresolved_inference_ids)


def _has_news_source(
    source_ids: list[str],
    *,
    source_rows_by_id: dict[str, dict[str, Any]],
) -> bool:
    """Whether a record retains at least one material news-row source."""

    for source_id in source_ids:
        source = source_rows_by_id.get(source_id)
        if not isinstance(source, dict):
            continue
        source_type = _first_string(source.get("source_type"), source.get("logical_role"))
        if source_type is not None and source_type.lower() in {
            "news_csv_row",
            "news_row",
            "news_article",
        }:
            return True
        if _string_list(source.get("input_row_ids")):
            return True
        # Legacy source ledgers may omit source_type/input_row_ids while
        # retaining the canonical NEWS row identity and article payload.  The
        # prefix is a structural row namespace, not a ticker or content rule;
        # require article fields as a second guard before treating it as
        # material news provenance.
        row_identity = _first_string(
            source.get("source_id"),
            source.get("source_row_id"),
            source.get("row_id"),
        )
        if (
            row_identity is not None
            and row_identity.startswith("NEWS-")
            and _first_string(source.get("title"), source.get("normalized_title")) is not None
            and isinstance(source.get("body"), str)
        ):
            return True
    return False


def _downgrade_unresolved_reference_training(
    record: dict[str, Any],
    *,
    reason: str = "unresolved_postmortem_fact_inference_reference",
) -> None:
    if record.get("training_eligible") is not True:
        return
    record["training_eligible"] = False
    record["sample_weight"] = 0.0
    record["training_exclusion_reason"] = reason
    prior = _first_string(record.get("eligibility_reason"))
    suffix = reason
    record["eligibility_reason"] = f"{prior}; {suffix}" if prior else suffix


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
    record_id = (
        _first_string(
            repaired.get("record_id"),
            repaired.get("brain_delta_id"),
        )
        or f"BD-{index:06d}"
    )
    repaired["record_id"] = record_id
    repaired["brain_delta_id"] = _first_string(repaired.get("brain_delta_id"), record_id)
    source_episode_id = _first_string(repaired.get("episode_id"))
    if source_episode_id is not None and source_episode_id != episode_id:
        repaired["legacy_source_episode_id"] = source_episode_id
    repaired["episode_id"] = episode_id
    repaired["trade_date"] = _first_string(repaired.get("trade_date"), trade_date)
    repaired["available_from"] = _valid_available_from(
        repaired.get("available_from"),
        available_from,
    )
    related_tickers = _ordered_unique(
        [
            *_string_list(repaired.get("related_tickers")),
            *_string_list(payload.get("related_tickers")),
        ]
    )
    related_ticker = related_tickers[0] if len(related_tickers) == 1 else None
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
        related_ticker,
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

    unresolved_fact_ids, unresolved_inference_ids = _sanitize_unknown_typed_references(
        repaired,
        known_fact_ids=known_fact_ids,
        known_inference_ids=known_inference_ids,
    )
    if unresolved_fact_ids:
        # ``*_tokens`` is intentional: ``*_fact_ids`` would be re-read as a
        # live importer reference instead of preserved legacy evidence.
        repaired["legacy_unresolved_fact_tokens"] = _ordered_unique(
            [
                *_string_list(repaired.get("legacy_unresolved_fact_tokens")),
                *unresolved_fact_ids,
            ]
        )
    if unresolved_inference_ids:
        repaired["legacy_unresolved_inference_tokens"] = _ordered_unique(
            [
                *_string_list(repaired.get("legacy_unresolved_inference_tokens")),
                *unresolved_inference_ids,
            ]
        )
    if unresolved_fact_ids or unresolved_inference_ids:
        repaired["unresolved_reference_reason"] = (
            "typed_reference_not_present_in_bundle_ledger"
        )

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

    # A legacy direct-event row occasionally used ``direct_event_id`` for its
    # FACT id.  The importer correctly treats that suffix as an event
    # reference, so keep the original identity while moving it to an explicit
    # fact reference that closes against fact_ledger_blind.  No event payload
    # is fabricated; the original token is retained in the audit fields.
    moved_direct_fact_ids: list[str] = []
    for container in (repaired, payload):
        direct_event_id = _first_string(container.get("direct_event_id"))
        if direct_event_id is None or direct_event_id not in known_fact_ids:
            continue
        container["direct_event_fact_id"] = direct_event_id
        container.pop("direct_event_id", None)
        moved_direct_fact_ids.append(direct_event_id)
    if moved_direct_fact_ids:
        repaired["legacy_mistyped_event_reference_values"] = _ordered_unique(
            [
                *_string_list(repaired.get("legacy_mistyped_event_reference_values")),
                *moved_direct_fact_ids,
            ]
        )
        repaired["related_domain_ids"] = _ordered_unique(
            [*_string_list(repaired.get("related_domain_ids")), *moved_direct_fact_ids]
        )

    source_candidates = _source_reference_candidates(repaired, payload)
    unresolved_source_ids = _ordered_unique(
        [
            candidate
            for candidate in source_candidates
            if _resolve_known_source_id(candidate, known_source_ids) is None
        ]
    )
    if unresolved_source_ids:
        repaired["legacy_unresolved_source_tokens"] = _ordered_unique(
            [
                *_string_list(repaired.get("legacy_unresolved_source_tokens")),
                *unresolved_source_ids,
            ]
        )
        repaired["unresolved_reference_reason"] = (
            "source_reference_not_present_in_bundle_ledger"
        )
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

    unresolved_inference_dependencies = [
        inference_id
        for inference_id in inference_ids
        if inference_id not in inference_fact_ids_by_id
    ]
    if unresolved_fact_ids and unresolved_inference_dependencies:
        # A known inference is not training evidence when its declared fact
        # dependency is absent from the embedded fact ledger. Keep all legacy
        # tokens and direct sources for audit, but fail the record closed.
        _downgrade_unresolved_reference_training(repaired)
    elif (
        (unresolved_fact_ids or unresolved_inference_ids)
        and not fact_ids
        and not inference_ids
        and not _has_news_source(source_ids, source_rows_by_id=source_rows_by_id)
    ):
        _downgrade_unresolved_reference_training(repaired)
    elif (
        unresolved_source_ids
        and not _has_news_source(source_ids, source_rows_by_id=source_rows_by_id)
    ):
        # A prior-day context source may be referenced but not embedded in
        # this episode's ledger. Preserve that token and record, but never
        # expose a placeholder-only source as a positive training example.
        _downgrade_unresolved_reference_training(
            repaired,
            reason="unresolved_provenance_source_reference",
        )

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
    elif repaired.get("training_eligible") is True:
        # Legacy eligible rows occasionally omit the weight.  Preserve the
        # research decision and give the importer a deterministic neutral
        # weight; later provenance/semantic gates may explicitly downgrade it.
        repaired["sample_weight"] = 1.0
    else:
        repaired["sample_weight"] = 0.0

    if repaired.get("record_type") == "event_ticker_edge":
        _repair_event_ticker_edge_cutoff(
            repaired,
            source_rows_by_id=source_rows_by_id,
        )
    _normalize_known_record_scalar_types(repaired, payload=payload)
    _standardize_custom_record_type(repaired, payload=payload)
    if (
        repaired.get("record_type") == "company_memory_delta"
        and _first_string(repaired.get("known_at"), payload.get("known_at")) is None
    ):
        repaired["known_at"] = _company_memory_known_at(repaired["available_from"])
    compacted = _compact(repaired)
    _normalize_ineligible_known_null_outcome(
        compacted,
        source_record=repaired,
        payload=payload,
    )
    return compacted


def _normalize_known_record_scalar_types(
    record: dict[str, Any],
    *,
    payload: dict[str, Any],
) -> None:
    """Canonicalize legacy scalar spellings without changing research meaning."""

    nested_label_quality = payload.get("label_quality")
    if "label_quality" not in record and isinstance(nested_label_quality, str):
        normalized_label_quality = nested_label_quality.lower()
        if normalized_label_quality == "verified_normal_day":
            normalized_label_quality = "verified"
        record["label_quality"] = normalized_label_quality
    if _first_string(record.get("record_type")) != "research_question":
        return
    nested_question = payload.get("question")
    if isinstance(nested_question, dict):
        # A legacy writer wrapped the typed research-question fields inside
        # payload.question.  Promote only the model's declared scalar/list
        # fields while retaining the complete nested payload unchanged.
        for field in (
            "question_id",
            "question",
            "status",
            "priority",
            "answerable_after",
            "related_record_ids",
        ):
            if field not in record and field in nested_question:
                record[field] = deepcopy(nested_question[field])
    for container in (record, payload):
        priority = container.get("priority")
        if isinstance(priority, (int, float)) and not isinstance(priority, bool):
            container["priority"] = str(priority)


def _normalize_ineligible_known_null_outcome(
    record: dict[str, Any],
    *,
    source_record: dict[str, Any],
    payload: dict[str, Any],
) -> None:
    """Keep an explicit null raw outcome while providing a valid typed mirror."""

    record_type = _first_string(record.get("record_type"))
    payload_model = KNOWN_RECORD_PAYLOAD_MODELS.get(record_type or "")
    if payload_model is None or "D_outcome" not in payload_model.model_fields:
        return
    if source_record.get("training_eligible") is True:
        return
    explicit_null = (
        "D_outcome" in source_record and source_record.get("D_outcome") is None
    ) or ("D_outcome" in payload and payload.get("D_outcome") is None)
    if explicit_null:
        record["D_outcome"] = {}


def _standardize_custom_record_type(
    record: dict[str, Any],
    *,
    payload: dict[str, Any],
) -> None:
    original_type = _first_string(record.get("record_type"))
    if original_type is None:
        return
    source_record_type = original_type
    company_name_payload = record.get("company_name")
    if isinstance(company_name_payload, dict):
        extracted_company_name = _first_string(
            company_name_payload.get("company_name"),
            company_name_payload.get("issuer_name"),
            company_name_payload.get("name"),
            company_name_payload.get("company"),
        )
        if extracted_company_name is not None:
            record["legacy_company_name_payload"] = deepcopy(company_name_payload)
            record["company_name"] = extracted_company_name
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
    )
    if ticker:
        record["ticker"] = ticker
    if company_name:
        record["company_name"] = company_name
    normalized_type = original_type.strip().lower()
    if original_type in KNOWN_RECORD_PAYLOAD_MODELS:
        return
    if normalized_type in KNOWN_RECORD_PAYLOAD_MODELS:
        record["legacy_record_type"] = original_type
        record["record_type"] = normalized_type
        return
    if (
        normalized_type not in _KNOWN_LEGACY_RECORD_TYPES
        and normalized_type not in _REPAIR_ONLY_RECORD_TYPES
    ):
        return
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
        "issuer_day_final_watchlist_supervision",
        "issuer_day_candidate_outcome",
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
        "direct_event_fact_outcome",
        "direct_event_hit_pattern",
        "direct_event_case",
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
            payload.get("response_label"),
            payload.get("label"),
        )
        record["label_quality"] = "verified"
        record["attribution_status"] = "postseal_label_attached_to_sealed_direct_event"
    elif original_type == "counterfactual_pair":
        selected = _as_dict(payload.get("selected"))
        missed = _as_dict(payload.get("missed_leader"))
        selected_ticker = _first_string(selected.get("ticker"))
        missed_ticker = _first_string(missed.get("ticker"))
        if selected_ticker and missed_ticker and selected_ticker != missed_ticker:
            record["legacy_record_type"] = original_type
            record["record_type"] = "blind_leader_preference_pair"
            record["blind_pair_id"] = _first_string(
                record.get("blind_pair_id"),
                record.get("record_id"),
                record.get("brain_delta_id"),
            )
            record["blind_preferred_ticker"] = selected_ticker
            record["blind_preferred_company_name"] = _first_string(
                selected.get("company_name"),
                selected.get("issuer_name"),
                selected.get("name"),
            )
            record["blind_rejected_ticker"] = missed_ticker
            record["blind_rejected_company_name"] = _first_string(
                missed.get("company_name"),
                missed.get("issuer_name"),
                missed.get("name"),
            )
            record["outcome_winner_ticker"] = missed_ticker
            record["outcome_winner_company_name"] = record.get(
                "blind_rejected_company_name"
            )
            record["blind_preference_correct"] = False
            record["training_target"] = "outcome_preferred_candidate"
            record["training_mode"] = "postseal_counterfactual_pair"
            record["correction_mode"] = _first_string(
                payload.get("comparison_axis"),
                "counterfactual_pair",
            )
        else:
            _downgrade_unresolved_reference_training(
                record,
                reason="counterfactual_pair_missing_distinct_source_tickers",
            )
    elif (
        original_type == "outcome_leader_case"
        and record.get("training_eligible") is True
        and payload.get("blind_selected") is False
        and payload.get("premarket_news_state") == "NEWS_PRESENT_NOT_SELECTED"
        and ticker is not None
    ):
        record["legacy_record_type"] = original_type
        record["record_type"] = "candidate_generation_error_case"
        record["error_id"] = _first_string(
            record.get("error_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
        record["error_type"] = payload["premarket_news_state"]
        record["correction_mode"] = payload["premarket_news_state"]
        record["missed_ticker"] = ticker
        if company_name is not None:
            record["missed_company_name"] = company_name
        record["training_target"] = "candidate_generation_correction"
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
            outcome.get("high_return_pct")
            if outcome.get("high_return_pct") is not None
            else record.get("D_high_return_pct")
            if record.get("D_high_return_pct") is not None
            else payload.get("D_high_return_pct"),
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
    elif original_type == "blind_false_positive":
        record["legacy_record_type"] = original_type
        record["record_type"] = "negative_control_case"
        record["training_target"] = "candidate_exclusion_calibration"
        record["rejection_or_exclusion_reason"] = _first_string(
            record.get("error_type"),
            record.get("error_reason"),
            payload.get("error_type"),
            "blind_false_positive",
        )
        record["lesson"] = _first_string(
            record.get("lesson"),
            record.get("lesson_signal"),
            payload.get("lesson"),
            payload.get("lesson_signal"),
        )
        outcome = _record_outcome(record, payload)
        record["outcome_high_return_pct"] = _float_or_none(
            outcome.get("high_return_pct")
            if outcome.get("high_return_pct") is not None
            else record.get("D_high_return_pct")
            if record.get("D_high_return_pct") is not None
            else payload.get("D_high_return_pct"),
        )
        record["upper_limit_touched"] = outcome.get("upper_limit_touched")
    elif original_type == "pairwise_rank_delta":
        record["legacy_record_type"] = original_type
        record["record_type"] = "ranking_error_case"
        record["training_target"] = "candidate_ranking_correction"
        record["error_id"] = _first_string(
            record.get("error_id"),
            record.get("record_id"),
            record.get("brain_delta_id"),
        )
        record["error_type"] = "pairwise_rank_delta"
        record["correction"] = _first_string(
            record.get("correction"),
            record.get("comparison_note"),
            payload.get("comparison_note"),
        )
        record["correction_mode"] = "pairwise_rank_delta"
        record["corrected_ticker"] = _first_string(
            record.get("corrected_ticker"),
            record.get("winner_ticker"),
            payload.get("winner_ticker"),
        )
        record["corrected_company_name"] = _first_string(
            record.get("corrected_company_name"),
            record.get("winner_company"),
            payload.get("winner_company"),
        )
        record["outcome_high_return_pct"] = _float_or_none(
            record.get("winner_high_return_pct")
            if record.get("winner_high_return_pct") is not None
            else payload.get("winner_high_return_pct"),
        )
    elif original_type in {
        "outcome_leader_reverse_audit_case",
        "outcome_leader_reverse_audit",
        "outcome_leader_reverse_audit_record",
        "outcome_leader_day",
        "outcome_leader_census_supervised",
        "missed_leader_error_case",
        "missed_outcome_leader",
        "missed_outcome_leader_audit",
        "missed_outcome_leader_analysis",
        "false_negative_outcome_leader",
        "outcome_leader_news_match",
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
        "selected_low_response_error",
        "ranking_error_pattern",
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
    elif original_type in {
        "newsless_outcome_case",
        "newsless_outcome_leader_case",
        "newsless_leader_control",
    }:
        record["legacy_record_type"] = original_type
        record["record_type"] = "newsless_or_unexplained_case"
        record.setdefault("training_target", "newsless_outcome_calibration")
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
    elif original_type in {"theme_case", "theme_outcome_case"}:
        record["legacy_record_type"] = original_type
        record["record_type"] = "theme_formation_case"
        record.setdefault("training_target", "theme_formation_response")
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
    elif original_type == "final_candidate_outcome":
        # This runner-specific label is an issuer/day outcome lane.  Keep the
        # original row and its evidence; only expose the importer canonical
        # type and training target.
        record["legacy_record_type"] = original_type
        record["record_type"] = "supervised_issuer_day_case"
        record["training_target"] = "issuer_day_price_response"
        record.setdefault("sample_weight", 1.0)
    elif original_type in {"cutline_exclusion_outcome", "semantic_guard_case"}:
        # Both lanes are explicit negative/audit examples.  They remain
        # training records when the source says so, but no new rejection
        # judgment is synthesized by repair.
        record["legacy_record_type"] = original_type
        record["record_type"] = "negative_control_case"
        record["training_target"] = "candidate_exclusion_calibration"
        record.setdefault("sample_weight", 1.0)
    elif original_type in {
        "hit_pattern_case",
        "overweighted_clean_fundamental_catalyst",
        "fresh_direct_contract_not_sufficient",
        "pattern_delta",
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
    if source_record_type != original_type and "legacy_record_type" in record:
        record["legacy_record_type"] = source_record_type


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
        payload.get("audit_decision"),
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
        source_correction_mode = _first_string(
            record.get("correction_mode"),
            payload.get("correction_mode"),
            record.get("error_mode"),
            payload.get("error_mode"),
        )
        if source_correction_mode is not None:
            record["correction_mode"] = source_correction_mode
        else:
            # Do not invent a semantic correction lesson for a missed leader.
            # The original label/error mode remains available in the record.
            record.pop("correction_mode", None)
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
        if legacy_record_type in {
            "outcome_leader_reverse_audit_case",
            "outcome_leader_reverse_audit",
            "outcome_leader_reverse_audit_record",
        }:
            # An audit classification such as NO_CUTOFF_NEWS_MATCH is not a
            # request to invent a training lesson or reclassify the record.
            # Preserve the first-class audit type and its payload verbatim;
            # eligibility/provenance normalization is handled by the outer
            # repair pass.
            record["record_type"] = legacy_record_type
            return
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
    # Keep detailed source relation labels in ``payload`` but provide the
    # canonical enum required by EventTickerEdgeRecord for direct import.
    record["relation_class"] = _event_ticker_edge_relation_class(record)
    if record.get("training_eligible") is not True:
        record["sample_weight"] = 0.0
        return
    source_ids = _string_list(record.get("provenance_source_ids"))
    valid_source_ids = [
        source_id for source_id in source_ids if _source_row_cutoff_valid(source_rows_by_id.get(source_id))
    ]
    if valid_source_ids:
        removed_source_ids = sorted(set(source_ids) - set(valid_source_ids))
        record["provenance_source_ids"] = valid_source_ids
        record["source_ids"] = valid_source_ids
        if removed_source_ids:
            record["provenance_source_filter"] = {
                "rule_id": "event_ticker_edge_cutoff_safe_sources.v1",
                "removed_source_ids": removed_source_ids,
                "retained_source_ids": valid_source_ids,
            }
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
    record["training_exclusion_reason"] = "missing_cutoff_provenance_for_event_ticker_edge"


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
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (f"REPAIRED-FINAL-{index:04d}")
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
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (f"REPAIRED-LEADER-{index:04d}")
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
        "training_target": ("beneficiary_discovery_response" if has_bound_news else "newsless_outcome_calibration"),
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
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (f"REPAIRED-CLUSTER-{index:04d}")
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
    record_id = _first_string(row.get("record_id"), row.get("brain_delta_id")) or (f"REPAIRED-UNKNOWN-{index:04d}")
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


def _exclude_semantically_invalid_training_records(
    records: list[dict[str, Any]],
    *,
    semantic_relation_ids: set[str],
) -> int:
    """Exclude only positive training records tied to an explicit semantic contradiction."""

    excluded_count = 0
    for record in records:
        matched_ids = record_semantic_exclusion_relation_ids(record, semantic_relation_ids)
        if record.get("training_eligible") is not True or not matched_ids:
            continue
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        record["training_exclusion_reason"] = "semantic_contract_failed"
        existing_reason = str(record.get("eligibility_reason") or "").strip()
        record["eligibility_reason"] = (
            f"{existing_reason}; semantic_contract_failed"
            if existing_reason and "semantic_contract_failed" not in existing_reason
            else existing_reason or "semantic_contract_failed"
        )
        record["semantic_exclusion_relation_ids"] = sorted(matched_ids)
        excluded_count += 1
    return excluded_count


def _exclude_unverifiable_outcome_training_records(
    records: list[dict[str, Any]],
) -> int:
    """Keep explicit audit rows while removing unusable outcome labels from training."""

    excluded_count = 0
    for record in records:
        if record.get("training_eligible") is not True:
            continue
        label = _first_string(
            record.get("label_quality"),
            _as_dict(record.get("payload")).get("label_quality"),
        )
        if label is None or label.strip().lower() not in {
            "missing",
            "unknown",
            "unverified",
        }:
            continue
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        record["training_exclusion_reason"] = "outcome_label_quality_unverified"
        prior = _first_string(record.get("eligibility_reason"))
        suffix = "outcome_label_quality_unverified"
        record["eligibility_reason"] = (
            f"{prior}; {suffix}" if prior and suffix not in prior else prior or suffix
        )
        excluded_count += 1
    return excluded_count


def _exclude_outcome_only_training_records(
    records: list[dict[str, Any]],
    *,
    source_rows_by_id: dict[str, dict[str, Any]],
) -> int:
    """Keep outcome-only context rows as memory, not positive training data.

    ``newsless`` and market/context records sometimes inherit an eligible
    flag from a legacy outcome census even though every provenance source is
    an outcome snapshot and no cutoff-safe news source is attached.  The
    snapshot remains fully preserved; only the unsafe training eligibility is
    downgraded with an explicit reason.
    """

    excluded = 0
    context_types = {
        "newsless_or_unexplained_case",
        "context_market_state_or_fact_case",
    }
    for record in records:
        payload = _as_dict(record.get("payload"))
        source_phase = str(
            record.get("source_phase") or payload.get("source_phase") or ""
        ).upper()
        retrospective_outcome_guard = (
            payload.get("blind_hit_upgrade_prohibited") is True
            and "RETROSPECTIVE" in source_phase
        )
        if record.get("training_eligible") is not True or (
            record.get("record_type") not in context_types
            and not retrospective_outcome_guard
        ):
            source_ids = set(_string_list(record.get("provenance_source_ids")))
            source_types = {
                str(
                    source_rows_by_id.get(source_id, {}).get("source_type")
                    or source_rows_by_id.get(source_id, {}).get("logical_role")
                    or ""
                ).upper()
                for source_id in source_ids
            }
            if not (
                record.get("training_eligible") is True
                and source_types
                and all(
                    _is_outcome_provenance_source_type(source_type)
                    for source_type in source_types
                )
            ):
                continue
        if retrospective_outcome_guard:
            record["training_eligible"] = False
            record["sample_weight"] = 0.0
            record["training_exclusion_reason"] = "outcome_only_or_nonstrong_label"
            prior_reason = _first_string(record.get("eligibility_reason"))
            record["eligibility_reason"] = (
                f"{prior_reason}; outcome_only_or_nonstrong_label"
                if prior_reason and "outcome_only_or_nonstrong_label" not in prior_reason
                else prior_reason or "outcome_only_or_nonstrong_label"
            )
            excluded += 1
            continue
        source_ids = set(_string_list(record.get("provenance_source_ids")))
        if not source_ids:
            continue
        source_types = {
            str(
                source_rows_by_id.get(source_id, {}).get("source_type")
                or source_rows_by_id.get(source_id, {}).get("logical_role")
                or ""
            ).upper()
            for source_id in source_ids
        }
        if not source_types or not all(
            _is_outcome_provenance_source_type(source_type)
            for source_type in source_types
        ):
            continue
        record["training_eligible"] = False
        record["sample_weight"] = 0.0
        record["training_exclusion_reason"] = "outcome_only_or_nonstrong_label"
        prior_reason = _first_string(record.get("eligibility_reason"))
        record["eligibility_reason"] = (
            f"{prior_reason}; outcome_only_or_nonstrong_label"
            if prior_reason and "outcome_only_or_nonstrong_label" not in prior_reason
            else prior_reason or "outcome_only_or_nonstrong_label"
        )
        excluded += 1
    return excluded


def _is_outcome_provenance_source_type(source_type: str) -> bool:
    """Recognize outcome-source aliases without relying on one legacy spelling."""

    normalized = source_type.strip().upper()
    return (
        normalized in {
            "RESEARCH_DAILY_OUTCOME_SNAPSHOT",
            "OUTCOME_SNAPSHOT",
            "D_RESPONSE_SNAPSHOT",
            "STOCK_OUTCOME_SNAPSHOT",
        }
        or "OUTCOME" in normalized
        or normalized.startswith("D_RESPONSE")
    )


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
        row.get("audit_result"),
        row.get("audit_status"),
        row.get("semantic_gate_status"),
        row.get("semantic_entailment"),
        row.get("semantic_result"),
        row.get("verdict"),
    )
    inferred_pass = (
        row.get("chain_complete") is True
        and row.get("quote_found_in_source_row") is True
        and not _string_list(row.get("fail_reasons"))
    )
    verdict_upper = verdict.upper() if verdict else ""
    explicit_boolean_pass = (
        (row.get("passed") is True or row.get("pass") is True)
        and not _string_list(row.get("fail_reasons"))
        and not verdict_upper.startswith("PASS_WITH")
    )
    corroborated_legacy_pass = (
        row.get("semantic_pass") is True
        and row.get("article_subject_local_predicate_owner_verified") is True
        and row.get("economic_mechanism_supported_verified") is True
        and row.get("forbidden_quote_role_detected") is False
        and _first_string(
            row.get("final_evidence_witness_id"),
            row.get("witness_id"),
        )
        is not None
        and not _string_list(row.get("fail_reasons"))
    )
    if verdict_upper in {"PASS", "PASSED"} or inferred_pass or explicit_boolean_pass or corroborated_legacy_pass:
        repaired["status"] = "PASS"
        repaired["semantic_verdict"] = "PASS"
        repaired["semantic_audit_status"] = "PASS"
    repaired.setdefault("ticker", _first_string(row.get("ticker"), row.get("code")))
    repaired.setdefault("company_name", _first_string(row.get("company_name"), row.get("name")))
    return _compact(repaired)


def _repair_candidate_semantic_alias_rows(
    rows: list[dict[str, Any]],
    *,
    entity_resolution_rows: list[dict[str, Any]],
    final_witness_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    final_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for final_witness in final_witness_rows:
        candidate_id = _first_string(final_witness.get("candidate_id"))
        if candidate_id is not None:
            final_by_candidate.setdefault(candidate_id, []).append(final_witness)
    entities_by_source: dict[str, list[dict[str, Any]]] = {}
    for entity in entity_resolution_rows:
        source_id = _first_string(
            entity.get("source_id"),
            entity.get("source_row_id"),
            entity.get("row_id"),
        )
        if source_id is not None:
            entities_by_source.setdefault(source_id, []).append(entity)

    repaired_rows: list[dict[str, Any]] = []
    for row in rows:
        candidate_id = _first_string(row.get("candidate_id"))
        source_id = _first_string(
            row.get("source_id"),
            row.get("source_row_id"),
            row.get("row_id"),
        )
        final_witnesses = final_by_candidate.get(candidate_id or "", [])
        entities = entities_by_source.get(source_id or "", [])
        matching = [
            (entity, final_witness)
            for entity in entities
            for final_witness in final_witnesses
            if _semantic_alias_evidence_matches(
                row,
                entity=entity,
                final_witness=final_witness,
            )
        ]
        if len(matching) != 1:
            repaired_rows.append(dict(row))
            continue
        entity, final_witness = matching[0]
        repaired = dict(row)
        repaired["local_predicate_owner_is_candidate"] = True
        repaired["target_issuer_is_article_subject"] = True
        repaired["semantic_alias_repair_provenance"] = {
            "rule_id": "semantic_owner_from_verified_historical_alias.v1",
            "candidate_id": candidate_id,
            "source_id": source_id,
            "ticker": _first_string(row.get("ticker")),
            "entity_resolution_id": _first_string(
                entity.get("entity_resolution_id"),
                entity.get("resolution_id"),
            ),
            "final_evidence_witness_id": _first_string(
                final_witness.get("final_evidence_witness_id"),
                final_witness.get("witness_id"),
            ),
        }
        repaired_rows.append(repaired)
    return repaired_rows


def _materialize_missing_candidate_semantic_witness_rows(
    rows: list[dict[str, Any]],
    *,
    screening_rows: list[dict[str, Any]],
    ranking_rows: list[dict[str, Any]],
    fact_rows: list[dict[str, Any]],
    inference_rows: list[dict[str, Any]],
    material_review_rows: list[dict[str, Any]],
    source_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Copy an omitted rankable witness from one exact existing evidence chain."""

    def unique_index(
        source_rows: list[dict[str, Any]],
        *fields: str,
    ) -> dict[str, dict[str, Any]]:
        candidates: dict[str, list[dict[str, Any]]] = {}
        for source_row in source_rows:
            for field in fields:
                value = _first_string(source_row.get(field))
                if value is not None:
                    candidates.setdefault(value, []).append(source_row)
        return {
            value: matches[0]
            for value, matches in candidates.items()
            if len(matches) == 1
        }

    screenings = unique_index(screening_rows, "screening_id")
    rankings = unique_index(
        ranking_rows,
        "source_screening_id",
        "screening_id",
    )
    facts = unique_index(fact_rows, "fact_id")
    inferences = unique_index(inference_rows, "inference_id")
    reviews = unique_index(material_review_rows, "material_review_id", "review_id")
    sources = unique_index(source_rows, "source_id", "source_row_id", "row_id")

    def modern_expected_values(screening_id: str) -> dict[str, Any] | None:
        screening = screenings.get(screening_id)
        ranking = rankings.get(screening_id)
        if screening is None or ranking is None:
            return None
        decision = str(screening.get("screening_decision") or "").upper()
        if decision not in _RANKABLE_SCREENING_DECISIONS and screening.get("rankable") is not True:
            return None
        fact_ids = _string_list(screening.get("source_fact_ids"))
        inference_ids = _string_list(screening.get("source_inference_ids"))
        review_ids = _string_list(
            screening.get("source_material_review_ids")
            or screening.get("material_review_ids")
        )
        if len(fact_ids) != 1 or len(inference_ids) != 1 or len(review_ids) != 1:
            return None
        fact = facts.get(fact_ids[0])
        inference = inferences.get(inference_ids[0])
        review = reviews.get(review_ids[0])
        if fact is None or inference is None or review is None:
            return None
        source_id = _first_string(fact.get("source_row_id"), fact.get("source_id"))
        source = sources.get(source_id or "")
        exact_quote = _first_string(fact.get("exact_quote"))
        semantic_witness = _first_string(screening.get("decision_reason_specific"))
        candidate_id = _first_string(screening.get("candidate_id"))
        company = _first_string(screening.get("company"), screening.get("candidate_company"))
        ticker = _first_string(screening.get("ticker"), screening.get("code"))
        inference_text = _first_string(
            inference.get("mechanism_sentence"),
            inference.get("statement"),
        )
        ranking_screening_id = _first_string(
            ranking.get("source_screening_id"),
            ranking.get("screening_id"),
        )
        if (
            source is None
            or exact_quote is None
            or semantic_witness is None
            or candidate_id is None
            or company is None
            or ticker is None
            or inference_text != semantic_witness
            or ranking_screening_id != screening_id
            or _first_string(ranking.get("candidate_id")) != candidate_id
            or _first_string(ranking.get("ticker"), ranking.get("code")) != ticker
            or _first_string(ranking.get("company"), ranking.get("candidate_company"))
            != company
            or _string_list(
                inference.get("source_fact_ids")
                or inference.get("supporting_fact_ids")
            )
            != fact_ids
            or inference.get("mechanism_supported") is not True
            or _first_string(review.get("source_id"), review.get("source_row_id"))
            != source_id
            or _first_string(review.get("exact_quote")) != exact_quote
            or fact.get("quote_found_in_source_row") is not True
            or review.get("quote_found_in_source_row") is not True
            or review.get("material_reviewed") is not True
        ):
            return None
        return {
            "candidate_id": candidate_id,
            "chain_complete": True,
            "company": company,
            "exact_quote": exact_quote,
            "fact_id": fact_ids[0],
            "inference_id": inference_ids[0],
            "material_review_id": review_ids[0],
            "screening_id": screening_id,
            "semantic_witness": semantic_witness,
            "source_id": source_id,
            "source_phase": _first_string(screening.get("source_phase")) or "BLIND",
            "ticker": ticker,
        }

    def legacy_expected_values(screening_id: str) -> dict[str, Any] | None:
        """Rebuild the compact CSW-* shape only from one closed ledger chain."""

        screening = screenings.get(screening_id)
        ranking = rankings.get(screening_id)
        if screening is None or ranking is None:
            return None
        decision = str(screening.get("screening_decision") or "").upper()
        if decision not in _RANKABLE_SCREENING_DECISIONS and screening.get("rankable") is not True:
            return None
        fact_ids = _string_list(screening.get("source_fact_ids"))
        inference_ids = _string_list(screening.get("source_inference_ids"))
        review_ids = _string_list(
            screening.get("source_material_review_ids")
            or screening.get("material_review_ids")
        )
        if len(fact_ids) != 1 or len(inference_ids) != 1 or len(review_ids) != 1:
            return None
        fact = facts.get(fact_ids[0])
        inference = inferences.get(inference_ids[0])
        review = reviews.get(review_ids[0])
        if fact is None or inference is None or review is None:
            return None
        source_id = _first_string(fact.get("source_row_id"), fact.get("source_id"))
        source = sources.get(source_id or "")
        exact_quote = _first_string(fact.get("exact_quote"))
        candidate_id = _first_string(screening.get("candidate_id"))
        company = _first_string(screening.get("company"), screening.get("candidate_company"))
        ticker = _first_string(screening.get("ticker"), screening.get("code"))
        inference_text = _first_string(
            inference.get("mechanism_sentence"),
            inference.get("statement"),
        )
        decision_reason = _first_string(screening.get("decision_reason_specific"))
        fact_class = _first_string(fact.get("fact_class"))
        reason_matches = bool(
            inference_text
            and (
                decision_reason == inference_text
                or (
                    fact_class is not None
                    and decision_reason == f"{fact_class}: {inference_text}"
                )
            )
        )
        review_closed = review.get("material_reviewed") is True or (
            review.get("materiality") is True
            and str(review.get("review_decision") or "").upper().startswith("ACCEPT")
        )
        if (
            source is None
            or exact_quote is None
            or candidate_id is None
            or not candidate_id.startswith("CAND-")
            or company is None
            or ticker is None
            or not reason_matches
            or _first_string(ranking.get("source_screening_id"), ranking.get("screening_id"))
            != screening_id
            or _first_string(ranking.get("candidate_id")) != candidate_id
            or _first_string(ranking.get("ticker"), ranking.get("code")) != ticker
            or _first_string(ranking.get("company"), ranking.get("candidate_company"))
            != company
            or _string_list(
                inference.get("source_fact_ids")
                or inference.get("supporting_fact_ids")
            )
            != fact_ids
            or inference.get("mechanism_supported") is not True
            or _first_string(review.get("source_id"), review.get("source_row_id"))
            != source_id
            or _first_string(review.get("exact_quote")) != exact_quote
            or fact.get("quote_found_in_source_row") is not True
            or review.get("quote_found_in_source_row") is not True
            or not review_closed
        ):
            return None
        return {
            "candidate_id": candidate_id,
            "exact_quote": exact_quote,
            "issuer_binding": {"company": company, "ticker": ticker},
            "semantic_witness_id": f"CSW-{candidate_id.removeprefix('CAND-')}",
            "semantic_witness_status": "CLOSED",
            "source_fact_ids": fact_ids,
            "source_ids": [source_id],
            "source_inference_ids": inference_ids,
            "source_material_review_ids": review_ids,
            "source_phase": _first_string(screening.get("source_phase")) or "BLIND",
            "source_screening_id": screening_id,
        }

    # Require the source's existing witnesses to prove the exact serialization
    # convention before filling any missing row in that same artifact.
    if not rows:
        return rows
    expected_values = modern_expected_values
    for candidate_builder in (modern_expected_values, legacy_expected_values):
        if all(
            (expected := candidate_builder(
                _first_string(row.get("screening_id"), row.get("source_screening_id"))
                or ""
            ))
            is not None
            and all(row.get(field) == value for field, value in expected.items())
            for row in rows
        ):
            expected_values = candidate_builder
            break
    else:
        return rows

    existing_screening_ids = {
        screening_id
        for row in rows
        for screening_id in [
            _first_string(row.get("screening_id"), row.get("source_screening_id"))
        ]
        if screening_id is not None
    }
    repaired = [dict(row) for row in rows]
    for screening_id in sorted(screenings):
        if screening_id in existing_screening_ids:
            continue
        expected = expected_values(screening_id)
        if expected is None:
            continue
        screening = screenings[screening_id]
        ranking = rankings[screening_id]
        fact_id = _string_list(screening.get("source_fact_ids"))[0]
        inference_id = _string_list(screening.get("source_inference_ids"))[0]
        review_id = _string_list(
            screening.get("source_material_review_ids")
            or screening.get("material_review_ids")
        )[0]
        fact = facts[fact_id]
        inference = inferences[inference_id]
        review = reviews[review_id]
        source_id = _first_string(fact.get("source_row_id"), fact.get("source_id"))
        assert source_id is not None
        source = sources[source_id]
        expected.update(
            {
                "candidate_semantic_witness_repair_provenance": {
                    "rule_id": "candidate_semantic_witness_from_unique_rankable_chain.v1",
                    "screening_id": screening_id,
                    "screening_sha256": sha256_text(canonical_json(screening)),
                    "ranking_sha256": sha256_text(canonical_json(ranking)),
                    "fact_sha256": sha256_text(canonical_json(fact)),
                    "inference_sha256": sha256_text(canonical_json(inference)),
                    "material_review_sha256": sha256_text(canonical_json(review)),
                    "source_row_sha256": sha256_text(canonical_json(source)),
                },
            }
        )
        if expected_values is modern_expected_values:
            expected["witness_id"] = f"CW-REPAIR-{screening_id}"
        repaired.append(expected)
    return repaired


def _repair_semantic_primary_fact_references(
    candidate_rows: list[dict[str, Any]],
    final_rows: list[dict[str, Any]],
    *,
    screening_rows: list[dict[str, Any]],
    fact_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Correct a witness only when one declared fact uniquely names its candidate."""

    screenings_by_id = {
        screening_id: row
        for row in screening_rows
        for screening_id in [_first_string(row.get("screening_id"))]
        if screening_id is not None
    }
    screenings_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for screening in screening_rows:
        candidate_id = _first_string(screening.get("candidate_id"))
        if candidate_id is not None:
            screenings_by_candidate.setdefault(candidate_id, []).append(screening)
    facts_by_id = {
        fact_id: row
        for row in fact_rows
        for fact_id in [_first_string(row.get("fact_id"))]
        if fact_id is not None
    }

    def repair_row(row: dict[str, Any]) -> dict[str, Any]:
        screening_id = _first_string(
            row.get("screening_id"),
            row.get("source_screening_id"),
        )
        screening = screenings_by_id.get(screening_id or "")
        if screening is None:
            candidate_id = _first_string(row.get("candidate_id"))
            candidates = screenings_by_candidate.get(candidate_id or "", [])
            if len(candidates) == 1:
                screening = candidates[0]
                screening_id = _first_string(screening.get("screening_id"))
        if screening is None or screening_id is None:
            return dict(row)

        candidate_company = _first_string(
            row.get("candidate_company"),
            row.get("company"),
            screening.get("company"),
        )
        company_surface = _semantic_company_surface(candidate_company)
        declared_fact_ids = _string_list(screening.get("source_fact_ids"))
        primary_fact_id = _first_string(
            row.get("primary_fact_id"),
            row.get("source_fact_id"),
            row.get("fact_id"),
        )
        if (
            not company_surface
            or primary_fact_id not in declared_fact_ids
            or primary_fact_id not in facts_by_id
        ):
            return dict(row)
        primary_quote = _first_string(
            facts_by_id[primary_fact_id].get("exact_quote"),
            row.get("primary_quote"),
        )
        if company_surface in _semantic_company_surface(primary_quote):
            return dict(row)

        matching_fact_ids = [
            fact_id
            for fact_id in declared_fact_ids
            if fact_id in facts_by_id
            and company_surface
            in _semantic_company_surface(facts_by_id[fact_id].get("exact_quote"))
        ]
        if len(matching_fact_ids) != 1:
            return dict(row)
        replacement_fact_id = matching_fact_ids[0]
        replacement_fact = facts_by_id[replacement_fact_id]
        replacement_quote = _first_string(replacement_fact.get("exact_quote"))
        replacement_source_id = _first_string(
            replacement_fact.get("source_row_id"),
            replacement_fact.get("source_id"),
        )
        if replacement_quote is None or replacement_source_id is None:
            return dict(row)

        repaired = dict(row)
        repaired["primary_fact_id"] = replacement_fact_id
        repaired["primary_quote"] = replacement_quote
        if "source_id" in repaired and "source_row_id" not in repaired:
            repaired["source_id"] = replacement_source_id
        else:
            repaired["source_row_id"] = replacement_source_id
        repaired["semantic_fact_reference_repair_provenance"] = {
            "rule_id": "primary_fact_from_unique_declared_candidate_surface.v1",
            "candidate_id": _first_string(row.get("candidate_id")),
            "screening_id": screening_id,
            "candidate_company": candidate_company,
            "prior_primary_fact_id": primary_fact_id,
            "replacement_primary_fact_id": replacement_fact_id,
            "replacement_fact_sha256": sha256_text(canonical_json(replacement_fact)),
        }
        return repaired

    return (
        [repair_row(row) for row in candidate_rows],
        [repair_row(row) for row in final_rows],
    )


def _semantic_company_surface(value: Any) -> str:
    return "".join(character.lower() for character in str(value or "") if character.isalnum())


def _semantic_alias_evidence_matches(
    row: dict[str, Any],
    *,
    entity: dict[str, Any],
    final_witness: dict[str, Any],
) -> bool:
    candidate_id = _first_string(row.get("candidate_id"))
    candidate_company = _first_string(
        row.get("candidate_company"),
        row.get("company"),
    )
    ticker = _first_string(row.get("ticker"))
    final_eligible = (
        row.get("candidate_final_eligible") is True
        or row.get("final_eligible_semantic") is True
        or row.get("final_eligible") is True
    )
    return all(
        (
            final_eligible,
            row.get("semantic_verdict") in {"PASS", "PASSED"},
            row.get("local_predicate_owner_is_candidate") is False,
            row.get("target_issuer_is_article_subject") is False,
            candidate_id is not None,
            candidate_company is not None,
            ticker is not None,
            entity.get("local_ticker_ownership_verified") is True,
            str(entity.get("resolution_status") or "").startswith("RESOLVED"),
            _first_string(entity.get("canonical_company")) == candidate_company,
            _first_string(entity.get("ticker")) == ticker,
            _first_string(entity.get("local_predicate_owner"))
            == _first_string(row.get("local_predicate_owner")),
            _first_string(final_witness.get("candidate_id")) == candidate_id,
            _first_string(final_witness.get("candidate_company")) == candidate_company,
            _first_string(final_witness.get("ticker")) == ticker,
            _first_string(final_witness.get("primary_fact_id"))
            == _first_string(row.get("primary_fact_id")),
            _first_string(final_witness.get("primary_quote"))
            == _first_string(row.get("primary_quote")),
            final_witness.get("local_predicate_owner_is_candidate") is True,
            final_witness.get("target_issuer_is_article_subject") is True,
            final_witness.get("issuer_role_anchor_valid") is True,
            final_witness.get("semantic_verdict") in {"PASS", "PASSED"},
        )
    )


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
    quarantine_status: str | None = None,
) -> dict[str, Any]:
    repaired = dict(episode)
    quarantined = quarantine_status is not None
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
            "bundle_status": quarantine_status or "ACCEPT_FULL",
            "brain_eligible": not quarantined,
            "direct_brain_ingest_ready": not quarantined,
            "automated_import_expected_to_pass": not quarantined,
            "embedded_attestation_authoritative": False,
            "external_quality_gate_required": False,
            "brain_ingest_blocked": quarantined,
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
    quarantine_status: str | None = None,
) -> dict[str, Any]:
    repaired = dict(old_validation)
    quarantined = quarantine_status is not None
    repaired.update(
        {
            "schema_version": "nslab.validation_report.v23",
            "episode_id": episode_id,
            "passed": not quarantined,
            "status": "QUARANTINE_PRESERVED" if quarantined else "PASS",
            "bundle_status": quarantine_status or "ACCEPT_FULL",
            "brain_eligible": not quarantined,
            "direct_brain_ingest_ready": not quarantined,
            "automated_import_expected_to_pass": not quarantined,
            "embedded_attestation_authoritative": False,
            "external_quality_gate_required": False,
            "validator_exit_code": 2 if quarantined else 0,
            "critical_error_count": 1 if quarantined else 0,
            "brain_ingest_blocked": quarantined,
            "computed_counts": {
                "brain_delta_record_count": record_count,
                "training_eligible_record_count": training_count,
            },
            "sample_weight_validation_status": sample_weight_summary["status"],
            "sample_weight_validation": sample_weight_summary,
            "issuer_day_weight_sum_mismatches": sample_weight_summary["issuer_day_weight_sum_mismatches"],
            "direct_event_weight_sum_mismatches": sample_weight_summary["direct_event_weight_sum_mismatches"],
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
    quarantine_status: str | None = None,
) -> dict[str, Any]:
    quarantined = quarantine_status is not None
    return {
        "schema_version": "nslab.direct_ingest_contract.v1",
        "episode_id": episode_id,
        "brain_eligible": not quarantined,
        "direct_brain_ingest_ready": not quarantined,
        "automated_import_expected_to_pass": not quarantined,
        "embedded_attestation_authoritative": False,
        "external_quality_gate_required": False,
        "requires_human_semantic_review": quarantined,
        "bundle_status": quarantine_status or "ACCEPT_FULL",
        "fatal_blockers": ["SOURCE_DECLARED_QUARANTINE"] if quarantined else [],
        "brain_ingest_blocked": quarantined,
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
            "issuer_day_weight_sum_mismatches": sample_weight_summary["issuer_day_weight_sum_mismatches"],
            "direct_event_weight_sum_mismatches": sample_weight_summary["direct_event_weight_sum_mismatches"],
            "validator_exit_code": 2 if quarantined else 0,
            "critical_error_count": 1 if quarantined else 0,
        },
    }


def _declared_quarantine_status(front: dict[str, Any]) -> str | None:
    """Return an explicit unsafe-run status without re-adjudicating BLIND data."""

    for field in ("bundle_status", "status"):
        value = _first_string(front.get(field))
        if value and value.upper().startswith(("QUARANTINE", "BLOCKED")):
            return value
    if (
        front.get("blind_valid") is False
        and front.get("brain_eligible") is False
        and front.get("outcome_research_performed") is True
    ):
        return "QUARANTINE_DECLARED_BLIND_INVALID"
    return None


def _repair_front_matter(
    front: dict[str, Any],
    *,
    episode_id: str,
    trade_date: str,
    available_from: str | None,
    record_count: int,
    training_count: int,
    quarantine_status: str | None = None,
) -> dict[str, Any]:
    repaired = dict(front)
    quarantined = quarantine_status is not None
    repaired.update(
        {
            "schema_version": "nslab.research_bundle.v11",
            "artifact_type": "research_episode_bundle",
            "episode_id": episode_id,
            "trade_date": trade_date,
            "available_from": available_from,
            "bundle_status": quarantine_status or "ACCEPT_FULL",
            "brain_eligible": not quarantined,
            "direct_brain_ingest_ready": not quarantined,
            "automated_import_expected_to_pass": not quarantined,
            "embedded_attestation_authoritative": False,
            "external_quality_gate_required": False,
            "validator_exit_code": 2 if quarantined else 0,
            "critical_error_count": 1 if quarantined else 0,
            "brain_ingest_blocked": quarantined,
            "brain_delta_record_count": record_count,
            "training_eligible_record_count": training_count,
            "repair_tool": "news_scalping_lab.tools.repair_research_bundle",
            "repair_mode": "legacy_bundle_packaging_only",
        },
    )
    repaired.pop("repaired_at", None)
    return _compact(repaired)


def _bundle_manifest(
    old_manifest: dict[str, Any],
    *,
    episode_id: str,
    created_at: str | None,
    record_count: int,
    training_count: int,
    block_payloads: dict[str, str],
    quarantine_status: str | None = None,
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
    quarantined = quarantine_status is not None
    repaired.update(
        {
            "schema_version": "nslab.bundle_manifest.v23",
            "episode_id": episode_id,
            "created_at": created_at,
            "bundle_status": quarantine_status or "ACCEPT_FULL",
            "brain_eligible": not quarantined,
            "direct_brain_ingest_ready": not quarantined,
            "automated_import_expected_to_pass": not quarantined,
            "embedded_attestation_authoritative": False,
            "external_quality_gate_required": False,
            "validator_exit_code": 2 if quarantined else 0,
            "critical_error_count": 1 if quarantined else 0,
            "brain_ingest_blocked": quarantined,
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
            payloads[name] = _strip_optional_fence(original_blocks[name])
    return payloads


def _strip_optional_fence(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) < 2:
        return block.strip()
    opening = re.match(r"^(?P<fence>`{3,}|~{3,})(?P<language>.*)$", lines[0].strip())
    if opening is None:
        return block.strip()
    fence = opening.group("fence")
    closing = lines[-1].strip()
    if (
        closing
        and closing[0] == fence[0]
        and len(closing) >= len(fence)
        and set(closing) == {fence[0]}
    ):
        return "\n".join(lines[1:-1]).strip()
    return block.strip()


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


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".partial",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.read_text(encoding="utf-8")
        os.replace(temporary_path, path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _json_payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _jsonl_payload(rows: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) for row in rows)


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
    return {key: round(value, 12) for key, value in sorted(weights.items()) if abs(value - 1.0) > 0.000001}


def _known_ids(rows: list[dict[str, Any]], key: str) -> set[str]:
    return {value for row in rows for value in [_first_string(row.get(key))] if value is not None}


def _source_reference_aliases(
    rows: list[dict[str, Any]],
    *,
    reference_rows: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Return only deterministic aliases for an existing source-ledger row.

    Research sessions disagree on whether a news row is spelled
    ``SRC-000123``, ``SRC-NEWS-000123`` or ``SRC-NEWS-ROW-000123``.  Older
    ledgers also use the row key itself (for example ``NEWS-000123``).  These
    aliases are usable only when exactly one canonical source row owns them.
    Ambiguous aliases are deliberately left untouched so the importer can
    quarantine them instead of silently joining the wrong row.
    """
    canonical_ids = {
        source_id
        for row in rows
        for source_id in [_first_string(row.get("source_id"))]
        if source_id is not None
    }
    alias_targets: dict[str, set[str]] = {}
    for row in [*rows, *(reference_rows or [])]:
        source_id = _first_string(row.get("source_id"))
        if source_id is None:
            continue
        for field in (
            "row_id",
            "source_row_id",
            "news_source_id",
            "input_row_id",
            "input_row_ids",
        ):
            aliases_for_field = _string_list(row.get(field))
            scalar_alias = _first_string(row.get(field))
            if scalar_alias is not None and not aliases_for_field:
                aliases_for_field = [scalar_alias]
            for alias in aliases_for_field:
                if alias != source_id:
                    alias_targets.setdefault(alias, set()).add(source_id)

    suffix_targets: dict[str, set[str]] = {}
    for source_id in canonical_ids:
        match = re.search(r"-(\d+)$", source_id)
        if match is None:
            continue
        suffix_targets.setdefault(match.group(1), set()).add(source_id)

    aliases: dict[str, str] = {
        alias: next(iter(targets))
        for alias, targets in alias_targets.items()
        if len(targets) == 1 and alias not in canonical_ids
    }
    for suffix, targets in suffix_targets.items():
        if len(targets) != 1:
            continue
        canonical = next(iter(targets))
        for alias in (
            f"SRC-{suffix}",
            f"SRC-NEWS-{suffix}",
            f"SRC-NEWS-ROW-{suffix}",
        ):
            if alias not in canonical_ids and alias != canonical:
                aliases[alias] = canonical
    return aliases


def _material_review_reference_aliases(rows: list[dict[str, Any]]) -> dict[str, str]:
    """Normalize the observed ``MREV``/``MRV`` review-id spelling alias."""
    canonical_ids = {
        review_id
        for row in rows
        for review_id in [_first_string(row.get("material_review_id"))]
        if review_id is not None
    }
    aliases: dict[str, str] = {}
    for review_id in canonical_ids:
        match = re.fullmatch(r"MRV-(\d+)", review_id)
        if match is None:
            continue
        alias = f"MREV-{match.group(1)}"
        if alias not in canonical_ids:
            aliases[alias] = review_id
    return aliases


def _normalize_source_reference_aliases(
    value: Any,
    aliases: dict[str, str],
    *,
    _allow_alias: bool = False,
) -> Any:
    if isinstance(value, dict):
        return {
            key: _normalize_source_reference_aliases(
                item,
                aliases,
                _allow_alias=_is_reference_alias_field(key),
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _normalize_source_reference_aliases(item, aliases, _allow_alias=_allow_alias)
            for item in value
        ]
    if _allow_alias and isinstance(value, str):
        return aliases.get(value, value)
    return value


def _is_reference_alias_field(field: str) -> bool:
    normalized = field.lower()
    return normalized in {
        "source_id",
        "source_ids",
        "provenance_source_id",
        "provenance_source_ids",
        "source_ledger_id",
        "source_ledger_ids",
        "matched_source_id",
        "matched_source_ids",
        "news_source_id",
        "news_source_ids",
        "trigger_source_id",
        "trigger_source_ids",
        "material_review_id",
        "material_review_ids",
        "source_material_review_id",
        "source_material_review_ids",
    }


def _repair_source_ledger_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    repaired: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        # Legacy source ledgers commonly use source_row_id as the primary
        # source key while brain/fact records refer to the same value as
        # provenance_source_ids/source_id.  Materialize only this deterministic
        # alias; never replace a real source row with a placeholder because the
        # field name differs.
        if "source_id" not in current:
            source_row_id = _first_string(
                current.get("source_row_id"),
                current.get("row_id"),
            )
            if source_row_id is not None:
                current["source_id"] = source_row_id
        if (
            current.get("time_verified") is True
            and (current.get("within_declared_window") is True or current.get("used_in_blind") is True)
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

    def collect(value: Any) -> None:
        """Collect event references at any nesting depth without inventing IDs."""

        if isinstance(value, list):
            for item in value:
                collect(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            if key in {
                "event_id",
                "direct_event_id",
                "event_ids",
                "trigger_event_ids",
                "related_event_ids",
                "source_event_ids",
            }:
                referenced_event_ids.update(_string_list(item))
                singular = _first_string(item)
                if singular is not None:
                    referenced_event_ids.add(singular)
            collect(item)

    for record in records:
        collect(record)
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


def _normalize_mistyped_related_event_references(
    records: list[dict[str, Any]],
    *,
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> None:
    """Preserve non-event domain IDs without pretending they define events."""

    known_event_ids = _authoritative_event_definition_ids(
        json_blocks,
        jsonl_blocks,
    )
    known_domain_ids: set[str] = set()
    known_screening_ids: set[str] = set()
    for value in (records, *json_blocks.values(), *jsonl_blocks.values()):
        incidental_event_ids: set[str] = set()
        _collect_singular_domain_ids(
            value,
            known_event_ids=incidental_event_ids,
            known_domain_ids=known_domain_ids,
        )
        _collect_defined_screening_ids(value, known_screening_ids)

    # Only fields whose contract explicitly permits a domain-reference alias
    # are normalized here.  A legacy case may call screening IDs
    # ``all_event_ids`` (or ``event_ids``), but the same values are also
    # present in its explicit ``screening_ids`` field.  Move only IDs that
    # are known screening/domain IDs and absent from the embedded event
    # population; genuine event IDs remain untouched.
    event_reference_fields: dict[str, str | None] = {
        "related_event_ids": "related_domain_ids",
        "blind_event_ids": None,
        "selected_blind_event_ids": "selected_blind_screening_ids",
        "all_event_ids": "screening_ids",
        "event_ids": "screening_ids",
        # Some postmortem ranking rows call observation/domain IDs
        # ``missed_more_relevant_event_ids``.  Keep those IDs, but do not
        # expose them to the importer as unresolved event references.
        "missed_more_relevant_event_ids": "missed_more_relevant_domain_ids",
        # Postmortem/counterexample rows sometimes call case IDs
        # ``sealed_event_ids``.  If the same token is declared by a case
        # artifact, preserve it as a domain reference instead of fabricating
        # an event placeholder that cannot be imported.
        "sealed_event_ids": "sealed_domain_ids",
    }
    singular_event_reference_fields = {
        # Legacy theme-formation rows use this field for a context/screening
        # domain token even when no authoritative event definition exists.
        "sealed_theme_event_id": "sealed_theme_domain_id",
    }
    screening_event_bindings = {
        (screening_id, event_id)
        for block_name, rows in jsonl_blocks.items()
        if "candidate_screening" in block_name.lower()
        for row in rows
        for screening_id in [_first_string(row.get("screening_id"))]
        for event_id in [_first_string(row.get("event_id"))]
        if screening_id is not None and event_id is not None
    }
    for record in records:
        moved_by_field: dict[str, list[str]] = {}
        moved_screenings_by_field: dict[str, list[str]] = {}
        for container in _dict_containers(record):
            for field, destination_field in event_reference_fields.items():
                event_ids = _string_list(container.get(field))
                if not event_ids:
                    continue
                mistyped = [
                    reference_id
                    for reference_id in event_ids
                    if reference_id not in known_event_ids
                    and reference_id in known_domain_ids
                ]
                if not mistyped:
                    continue
                moved_by_field.setdefault(field, []).extend(mistyped)
                mistyped_set = set(mistyped)
                container[field] = [
                    reference_id
                    for reference_id in event_ids
                    if reference_id not in mistyped_set
                ]
                destination_values = mistyped
                if destination_field in {
                    "screening_ids",
                    "selected_blind_screening_ids",
                }:
                    destination_values = [
                        reference_id
                        for reference_id in mistyped
                        if reference_id in known_screening_ids
                    ]
                    moved_screenings_by_field.setdefault(field, []).extend(
                        destination_values
                    )
                if destination_field is not None and destination_values:
                    container[destination_field] = _ordered_unique(
                        [
                            *_string_list(container.get(destination_field)),
                            *destination_values,
                        ]
                    )
            for field, destination_field in singular_event_reference_fields.items():
                reference_id = _first_string(container.get(field))
                if (
                    reference_id is None
                    or reference_id in known_event_ids
                    or reference_id not in known_domain_ids
                    or (
                        field == "sealed_theme_event_id"
                        and (
                            _first_string(container.get("source_screening_id")),
                            reference_id,
                        )
                        not in screening_event_bindings
                    )
                ):
                    continue
                moved_by_field.setdefault(field, []).append(reference_id)
                container.pop(field)
                container[destination_field] = reference_id

        if not moved_by_field:
            continue
        mistyped = [value for values in moved_by_field.values() for value in values]
        record["legacy_mistyped_event_reference_values"] = _ordered_unique(
            [
                *_string_list(record.get("legacy_mistyped_event_reference_values")),
                *mistyped,
            ]
        )
        record["related_domain_ids"] = _ordered_unique(
            [
                *_string_list(record.get("related_domain_ids")),
                *mistyped,
            ]
        )
        selected_screening_ids = moved_screenings_by_field.get(
            "selected_blind_event_ids", []
        )
        if selected_screening_ids:
            record["selected_blind_screening_ids"] = _ordered_unique(
                [
                    *_string_list(record.get("selected_blind_screening_ids")),
                    *selected_screening_ids,
            ]
        )


def _normalize_null_event_references(records: list[dict[str, Any]]) -> None:
    """Remove only explicit nulls from typed top-level event reference lists.

    Some legacy writers emitted ``event_ids: [null]`` to mean that no event
    was bound. The typed payload contract represents the same absence as an
    empty list. The receipt lets the quality audit prove that no non-null
    reference was removed or introduced.
    """

    event_fields = {
        "all_event_ids",
        "blind_event_ids",
        "event_ids",
        "missed_more_relevant_event_ids",
        "related_event_ids",
        "sealed_event_ids",
        "selected_blind_event_ids",
    }
    for record in records:
        repaired_fields: list[str] = []
        for field in sorted(event_fields):
            value = record.get(field)
            if not isinstance(value, list) or not any(item is None for item in value):
                continue
            record[field] = [item for item in value if item is not None]
            repaired_fields.append(field)
        if repaired_fields:
            record["repair_removed_null_event_reference_fields"] = repaired_fields


def _dict_containers(value: Any) -> list[dict[str, Any]]:
    """Return every nested mapping without interpreting its domain fields."""

    containers: list[dict[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            containers.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return containers


def _collect_singular_domain_ids(
    value: Any,
    *,
    known_event_ids: set[str],
    known_domain_ids: set[str],
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str) and item:
                if key == "event_id":
                    known_event_ids.add(item)
                elif key.endswith("_id"):
                    known_domain_ids.add(item)
            _collect_singular_domain_ids(
                item,
                known_event_ids=known_event_ids,
                known_domain_ids=known_domain_ids,
            )
    elif isinstance(value, list):
        for item in value:
            _collect_singular_domain_ids(
                item,
                known_event_ids=known_event_ids,
                known_domain_ids=known_domain_ids,
            )


def _authoritative_event_definition_ids(
    json_blocks: dict[str, Any],
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> set[str]:
    """Collect event identities only from artifacts that define events."""

    event_ids: set[str] = set()
    definition_tokens = ("event_ledger", "event_cluster", "row_disposition")
    for block_name, rows in jsonl_blocks.items():
        lower_name = block_name.lower()
        if any(token in lower_name for token in definition_tokens):
            _collect_event_definition_ids(rows, event_ids)
        elif "source_ledger" in lower_name:
            for row in rows:
                event_ids.update(_string_list(row.get("event_ids")))
    for block_name, payload in json_blocks.items():
        if any(token in block_name.lower() for token in definition_tokens):
            _collect_event_definition_ids(payload, event_ids)
    return event_ids


def _collect_event_definition_ids(value: Any, target: set[str]) -> None:
    if isinstance(value, dict):
        event_id = _first_string(value.get("event_id"))
        if event_id is not None:
            target.add(event_id)
        target.update(_string_list(value.get("event_ids")))
        for item in value.values():
            _collect_event_definition_ids(item, target)
    elif isinstance(value, list):
        for item in value:
            _collect_event_definition_ids(item, target)


def _collect_defined_screening_ids(value: Any, target: set[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"screening_id", "candidate_screening_id"}:
                target.update(_string_list(item))
                singular = _first_string(item)
                if singular is not None:
                    target.add(singular)
            _collect_defined_screening_ids(item, target)
    elif isinstance(value, list):
        for item in value:
            _collect_defined_screening_ids(item, target)


def _ordered_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


_NUMERIC_IDENTIFIER_FIELDS = frozenset(
    {
        "row_id",
        "source_row_id",
        "input_row_id",
        "direct_event_id",
        "direct_event_case_id",
        "candidate_event_id",
    }
)


def _normalize_numeric_identifier_fields(records: list[dict[str, Any]]) -> None:
    """Canonicalize numeric legacy IDs without changing their identity.

    A few research sessions serialize row/event identifiers as JSON numbers
    even though the direct-ingest contracts model them as strings. Convert
    only known identifier fields (including nested payload mirrors); never
    coerce market metrics, ranks, tickers, or arbitrary numeric values.
    """

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in list(value.items()):
                if key in _NUMERIC_IDENTIFIER_FIELDS:
                    if isinstance(item, int) and not isinstance(item, bool):
                        value[key] = str(item)
                    elif isinstance(item, list):
                        value[key] = [
                            str(entry)
                            if isinstance(entry, int) and not isinstance(entry, bool)
                            else entry
                            for entry in item
                        ]
                visit(value[key])
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for record in records:
        visit(record)


def _source_rows_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        source_id = _first_string(row.get("source_id"))
        if source_id is not None:
            indexed[source_id] = row
    return indexed


def _enrich_records_with_artifact_evidence(
    rows: list[dict[str, Any]],
    *,
    jsonl_blocks: dict[str, list[dict[str, Any]]],
    known_source_ids: set[str],
    known_fact_ids: set[str],
    known_inference_ids: set[str],
    fact_source_ids_by_id: dict[str, list[str]],
    inference_fact_ids_by_id: dict[str, list[str]],
) -> list[dict[str, Any]]:
    evidence_by_ticker = _artifact_evidence_by_ticker(
        jsonl_blocks,
        known_source_ids=known_source_ids,
        known_fact_ids=known_fact_ids,
        known_inference_ids=known_inference_ids,
        fact_source_ids_by_id=fact_source_ids_by_id,
        inference_fact_ids_by_id=inference_fact_ids_by_id,
    )
    if not evidence_by_ticker:
        return rows

    enriched: list[dict[str, Any]] = []
    for row in rows:
        current = dict(row)
        payload = _as_dict(current.get("payload"))
        ticker = _first_string(
            current.get("ticker"),
            current.get("code"),
            current.get("stock_code"),
            current.get("company_code"),
            current.get("issuer_code"),
            payload.get("ticker"),
            payload.get("code"),
            payload.get("stock_code"),
            payload.get("company_code"),
            payload.get("issuer_code"),
        )
        evidence = evidence_by_ticker.get(ticker or "")
        if evidence is not None:
            for field in ("source_ids", "fact_ids", "inference_ids"):
                merged = _merge_unique(_string_list(current.get(field)), evidence[field])
                if merged:
                    current[field] = merged
            merged_sources = _merge_unique(
                _string_list(current.get("provenance_source_ids")),
                evidence["source_ids"],
            )
            if merged_sources:
                current["provenance_source_ids"] = merged_sources
        enriched.append(current)
    return enriched


def _artifact_evidence_by_ticker(
    jsonl_blocks: dict[str, list[dict[str, Any]]],
    *,
    known_source_ids: set[str],
    known_fact_ids: set[str],
    known_inference_ids: set[str],
    fact_source_ids_by_id: dict[str, list[str]],
    inference_fact_ids_by_id: dict[str, list[str]],
) -> dict[str, dict[str, list[str]]]:
    indexed: dict[str, dict[str, list[str]]] = {}
    for block_name in (
        "forecast_scorecard.jsonl",
        "candidate_screening.jsonl",
        "issuer_day_supervised.jsonl",
        "direct_event_supervised.jsonl",
    ):
        for row in jsonl_blocks.get(block_name, []):
            ticker = _first_string(
                row.get("ticker"),
                row.get("code"),
                row.get("stock_code"),
                row.get("company_code"),
                row.get("issuer_code"),
            )
            if ticker is None:
                continue
            source_ids = _collect_source_ids_from_artifact_evidence(
                row,
                known_source_ids=known_source_ids,
            )
            fact_ids = _filter_known(
                [
                    *_string_list(row.get("fact_ids")),
                    *_string_list(row.get("source_fact_ids")),
                    *_string_list(row.get("blind_fact_ids")),
                    *_string_list(row.get("fact_id")),
                ],
                known_fact_ids,
            )
            inference_ids = _filter_known(
                [
                    *_string_list(row.get("inference_ids")),
                    *_string_list(row.get("source_inference_ids")),
                    *_string_list(row.get("blind_inference_ids")),
                    *_string_list(row.get("inference_id")),
                ],
                known_inference_ids,
            )
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
            entry = indexed.setdefault(
                ticker,
                {"source_ids": [], "fact_ids": [], "inference_ids": []},
            )
            entry["source_ids"] = _merge_unique(entry["source_ids"], source_ids)
            entry["fact_ids"] = _merge_unique(entry["fact_ids"], fact_ids)
            entry["inference_ids"] = _merge_unique(
                entry["inference_ids"],
                inference_ids,
            )
    return indexed


def _collect_source_ids_from_artifact_evidence(
    row: dict[str, Any],
    *,
    known_source_ids: set[str],
) -> list[str]:
    return _filter_known(
        [
            *_string_list(row.get("source_ids")),
            *_string_list(row.get("provenance_source_ids")),
            *_string_list(row.get("source_ledger_ids")),
            *_string_list(row.get("source_id")),
            *_string_list(row.get("news_source_id")),
        ],
        known_source_ids,
    )


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
    candidates = _source_reference_candidates(record, payload)
    seen: set[str] = set()
    source_ids: list[str] = []
    for candidate in candidates:
        resolved = _resolve_known_source_id(candidate, known_source_ids)
        if resolved is not None and resolved not in seen:
            source_ids.append(resolved)
            seen.add(resolved)
    return source_ids


def _resolve_known_source_id(
    candidate: str,
    known_source_ids: set[str],
) -> str | None:
    if not known_source_ids:
        return candidate
    if candidate in known_source_ids:
        return candidate
    # ``ROW-*``/``NEWS-*`` tokens are row identities, not new sources. A
    # unique numeric suffix match is the same alias contract used by the
    # source-ledger normalizer; other domain IDs (CAND/FACT/etc.) are never
    # guessed as sources.
    if not candidate.startswith(("ROW-", "NEWS-", "SRC-NEWS-")):
        return None
    match = re.search(r"-(\d+)$", candidate)
    if match is None:
        return None
    matches = [
        source_id
        for source_id in known_source_ids
        if (source_match := re.search(r"-(\d+)$", source_id)) is not None
        and source_match.group(1) == match.group(1)
    ]
    return matches[0] if len(matches) == 1 else None


def _source_reference_candidates(
    record: dict[str, Any],
    payload: dict[str, Any],
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
    return _ordered_unique(candidates)


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


def _event_ticker_edge_relation_class(record: dict[str, Any]) -> str:
    """Return the importer enum while retaining detailed labels in payload.

    Research bundles sometimes use a more specific relation label such as
    ``NAMED_ACQUISITION_TARGET``. That label is meaningful evidence, but it
    is not the canonical ``RelationClass`` enum accepted by the importer. A
    broad class is therefore derived from the already-normalized path type;
    the original detailed value remains untouched in the nested payload.
    """
    payload = _as_dict(record.get("payload"))
    existing = _first_string(
        record.get("relation_class"),
        payload.get("relation_class"),
    )
    if existing is not None and existing.upper() in EVENT_TICKER_EDGE_ALLOWED_PATH_TYPES:
        return existing.upper()
    return _event_ticker_edge_path_type(record)


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
    return bool(row and row.get("time_verified") is True and row.get("available_before_cutoff") is True)


def _filter_known(values: list[str], known: set[str]) -> list[str]:
    return _merge_unique([value for value in values if value in known])


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


def _bundle_cutoff(
    front: dict[str, Any],
    json_blocks: dict[str, Any],
) -> datetime | None:
    candidates: list[Any] = [
        front.get("cutoff_at"),
        front.get("cutoff_kst"),
        front.get("cutoff"),
    ]
    for block_name in (
        "blind_prediction.json",
        "phase_state.json",
        "research_episode.json",
    ):
        block = _as_dict(json_blocks.get(block_name))
        candidates.extend(
            (
                block.get("cutoff_at"),
                block.get("cutoff_kst"),
                block.get("cutoff"),
            )
        )
        if block_name == "research_episode.json":
            coverage = _as_dict(block.get("coverage"))
            candidates.append(coverage.get("expected_end"))
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate:
            continue
        try:
            return parse_datetime(candidate)
        except ValueError:
            continue
    return None


def _default_available_from(trade_date: str) -> str:
    trade_day = date.fromisoformat(trade_date[:10])
    next_day = trade_day + timedelta(days=1)
    return f"{next_day.isoformat()}T00:00:00+09:00"


def _resolve_available_from(
    front: dict[str, Any],
    old_manifest: dict[str, Any],
    episode: dict[str, Any],
    *,
    trade_date: str,
) -> str:
    explicit_available_from = _first_string(
        front.get("available_from"),
        old_manifest.get("available_from"),
        episode.get("available_from"),
    )
    next_trade_available_from = _next_trade_midnight(front, episode)
    # Legacy bundles sometimes copied the wall-clock research ``created_at``
    # into ``available_from`` (even years after the episode).  When the bundle
    # declares a next trade date, only retain an explicit availability value on
    # that same trading date; otherwise use the source-derived next-trade
    # midnight.  This is a temporal normalization, not a new research claim.
    if explicit_available_from and next_trade_available_from:
        try:
            explicit_date = datetime.fromisoformat(explicit_available_from).date()
            next_trade_date = datetime.fromisoformat(next_trade_available_from).date()
        except ValueError:
            explicit_available_from = None
        else:
            if explicit_date != next_trade_date:
                explicit_available_from = None
    return explicit_available_from or next_trade_available_from or _default_available_from(trade_date)


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


def _company_memory_known_at(available_from: str) -> str:
    """Materialize a timezone-aware known_at without changing its source day."""

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", available_from):
        return f"{available_from}T00:00:00+09:00"
    return available_from


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str) and item] if isinstance(value, list) else []


def _first_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _ledger_rows(
    jsonl_blocks: dict[str, list[dict[str, Any]]],
    prefix: str,
) -> list[dict[str, Any]]:
    """Return existing ledger rows across blind/legacy/postmortem variants."""

    rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    # Some bundles use ``postmortem_fact_ledger``/``postmortem_inference_ledger``
    # instead of the older ``fact_ledger_postmortem`` spelling.  Both are
    # existing source ledgers, so their IDs must participate in the same
    # reference-closure lookup; otherwise valid postmortem references are
    # downgraded as unresolved during repair.
    accepted_prefixes = (prefix, f"postmortem_{prefix}")
    names = sorted(
        name
        for name in jsonl_blocks
        if name.endswith(".jsonl")
        and any(name.startswith(item) for item in accepted_prefixes)
    )
    for name in names:
        for row in jsonl_blocks[name]:
            if not isinstance(row, dict):
                continue
            identifier_key = "fact_id" if prefix == "fact_ledger" else "inference_id"
            postmortem_identifier_key = f"postmortem_{identifier_key}"
            identifier = _first_string(
                row.get(identifier_key),
                row.get(postmortem_identifier_key),
            )
            if identifier is not None:
                if identifier in seen_ids:
                    continue
                seen_ids.add(identifier)
            normalized = dict(row)
            if identifier is not None:
                normalized.setdefault(identifier_key, identifier)
            rows.append(normalized)
    return rows


def _derive_episode_id(input_path: Path, trade_date: str) -> str:
    """Build a deterministic namespace for legacy bundles missing episode_id."""

    date_token = re.sub(r"[^0-9]", "", trade_date)[:8] or "UNKNOWN"
    source_digest = hashlib.sha256(input_path.read_bytes()).hexdigest()[:12]
    return f"NSLAB-{date_token}-{source_digest}"


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
        # ``payload`` is a first-class machine artifact boundary.  An empty
        # object is still an explicit source value and must survive repair;
        # dropping it makes source/repaired payload lineage look incomplete.
        if item == [] or (item == {} and key != "payload"):
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
