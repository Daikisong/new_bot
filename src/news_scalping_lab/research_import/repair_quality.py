"""Structural quality gates for repaired research bundles."""

from __future__ import annotations

import math
import re
from bisect import bisect_right
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from news_scalping_lab.records.preference import has_sealed_preference_pair
from news_scalping_lab.research_import.repair_census import artifact_rows, census_source
from news_scalping_lab.research_import.repair_models import (
    ArtifactRow,
    RecordLineageEntry,
    RepairQualityGate,
    RepairTaskState,
    SourceCensus,
)
from news_scalping_lab.research_import.repair_source_evidence import (
    NEWS_TIMESTAMP_REPAIR_RULE,
    audit_rehydrated_news_source_timestamps,
)
from news_scalping_lab.research_import.versioned_bundle import (
    inspect_versioned_bundle,
    parse_generic_bundle,
)
from news_scalping_lab.utils import canonical_json, parse_datetime, sha256_text

_CURRENT_GOLD_REQUIRED_BLOCKS = {
    "access_log.jsonl",
    "blind_prediction.json",
    "blind_seal_receipt.json",
    "brain_delta.jsonl",
    "candidate_ranking_audit.jsonl",
    "candidate_screening.jsonl",
    "candidate_semantic_witness.jsonl",
    "final_evidence_witness.jsonl",
    "final_semantic_audit.jsonl",
    "material_review.jsonl",
    "material_review_queue.jsonl",
    "outcome_leader_census.jsonl",
    "outcome_ledger.jsonl",
    "outcome_to_news_audit.jsonl",
    "record_provenance_closure_audit.jsonl",
    "row_disposition.jsonl",
    "semantic_regression_tests.jsonl",
    "source_ledger.jsonl",
    "beneficiary_discovery_cases.jsonl",
    "blind_leader_preference_pairs.jsonl",
    "candidate_generation_error_cases.jsonl",
    "context_market_state_or_fact_cases.jsonl",
    "direct_event_cases.jsonl",
    "issuer_day_cases.jsonl",
    "negative_control_cases.jsonl",
    "newsless_or_unexplained_cases.jsonl",
    "ranking_error_cases.jsonl",
    "theme_formation_cases.jsonl",
}
_RAW_RECORD_TYPE_TOKEN_BYTES = re.compile(rb"(?i)[\"']record_type[\"']\s*:")
_EVENT_REFERENCE_FIELDS = {
    "related_event_ids",
    "blind_event_ids",
    "selected_blind_event_ids",
    "all_event_ids",
    "event_ids",
    "missed_more_relevant_event_ids",
    "sealed_event_ids",
}
_SINGULAR_DOMAIN_EVENT_REFERENCE_FIELDS = {
    "sealed_theme_event_id": "sealed_theme_domain_id",
}

_MATERIAL_DISPOSITIONS = {
    "BODY_TABLE_OR_LIST_AUDIT",
    "D1_CONTINUATION_SIGNAL",
    "DIRECT_ISSUER_MATERIAL",
    "DIRECT_ISSUER_SECONDARY",
    "DISCLOSURE_OR_MARKET_NOTICE",
    "MARKET_STATE_REGIME",
    "PARSER_AMBIGUOUS_REVIEWED",
    "THEME_POLICY_INDUSTRY_EVENT",
}

_CASE_BLOCKS: dict[str, tuple[str, str]] = {
    "issuer_day_cases.jsonl": ("ISSUER_DAY", "issuer_day_case_id"),
    "direct_event_cases.jsonl": ("DIRECT_EVENT", "direct_event_case_id"),
    "theme_formation_cases.jsonl": ("THEME", "theme_case_id"),
    "blind_leader_preference_pairs.jsonl": ("PAIR", "pair_id"),
    "candidate_generation_error_cases.jsonl": ("CANDIDATE_GEN", "case_id"),
    "ranking_error_cases.jsonl": ("RANKING", "case_id"),
    "newsless_or_unexplained_cases.jsonl": ("NEWSLESS", "case_id"),
    "negative_control_cases.jsonl": ("NEGATIVE", "negative_control_id"),
    "beneficiary_discovery_cases.jsonl": ("BENEFICIARY", "beneficiary_case_id"),
    "context_market_state_or_fact_cases.jsonl": ("CONTEXT", "context_case_id"),
}

_CASE_RECORD_TYPES = {
    "ISSUER_DAY": "supervised_issuer_day_case",
    "DIRECT_EVENT": "supervised_direct_event_case",
    "THEME": "theme_formation_case",
    "PAIR": "blind_leader_preference_pair",
    "CANDIDATE_GEN": "candidate_generation_error_case",
    "RANKING": "ranking_error_case",
    "NEWSLESS": "newsless_or_unexplained_case",
    "NEGATIVE": "negative_control_case",
    "BENEFICIARY": "beneficiary_discovery_case",
    "CONTEXT": "context_market_state_or_fact_case",
}

_CASE_ID_ALIASES: dict[str, tuple[str, ...]] = {
    "issuer_day_cases.jsonl": ("issuer_day_case_id", "case_id"),
    "direct_event_cases.jsonl": ("direct_event_case_id", "case_id"),
    "theme_formation_cases.jsonl": (
        "theme_case_id",
        "theme_formation_case_id",
        "case_id",
    ),
    # Some legacy bundles call the same pair identity ``blind_pair_id``.
    # Treat it as an alias only; the source row remains authoritative and no
    # pair judgment is fabricated by the repair step.
    "blind_leader_preference_pairs.jsonl": (
        "pair_id",
        "sealed_pair_id",
        "blind_pair_id",
        "case_id",
    ),
    "candidate_generation_error_cases.jsonl": (
        "case_id",
        "candidate_generation_error_case_id",
    ),
    "ranking_error_cases.jsonl": ("case_id", "ranking_error_case_id"),
    "newsless_or_unexplained_cases.jsonl": ("case_id", "newsless_case_id"),
    "negative_control_cases.jsonl": (
        "negative_control_id",
        "negative_control_case_id",
        "case_id",
    ),
    "beneficiary_discovery_cases.jsonl": (
        "beneficiary_case_id",
        "beneficiary_discovery_case_id",
        "case_id",
    ),
    "context_market_state_or_fact_cases.jsonl": (
        "context_case_id",
        "context_market_state_or_fact_case_id",
        "case_id",
    ),
}

_DERIVED_CASE_TARGETS: dict[str, tuple[str, str]] = {
    "issuer_day_cases.jsonl": ("supervised_issuer_day_case", "issuer_day_case_id"),
    "direct_event_cases.jsonl": ("supervised_direct_event_case", "direct_event_case_id"),
    "blind_leader_preference_pairs.jsonl": (
        "blind_leader_preference_pair",
        "pair_id",
    ),
    "candidate_generation_error_cases.jsonl": (
        "candidate_generation_error_case",
        "candidate_generation_error_case_id",
    ),
    "ranking_error_cases.jsonl": ("ranking_error_case", "ranking_error_case_id"),
    "newsless_or_unexplained_cases.jsonl": (
        "newsless_or_unexplained_case",
        "newsless_case_id",
    ),
    "beneficiary_discovery_cases.jsonl": (
        "beneficiary_discovery_case",
        "beneficiary_case_id",
    ),
    "negative_control_cases.jsonl": ("negative_control_case", "negative_control_id"),
    "context_market_state_or_fact_cases.jsonl": (
        "context_market_state_or_fact_case",
        "context_case_id",
    ),
    # The artifact is named ``theme_formation_cases`` for historical reasons,
    # but the importer canonical type is the supervised variant.  Keeping the
    # canonical type aligned with the actual brain row breaks ties when the
    # same theme is also represented by a zero-weight context record.
    "theme_formation_cases.jsonl": (
        "supervised_theme_formation_case",
        "theme_case_id",
    ),
}

_ARTIFACT_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "source_ledger.jsonl": ("source_id", "source_row_id", "row_id"),
    "row_disposition.jsonl": ("row_id",),
    "material_review_queue.jsonl": (
        "material_review_queue_id",
        "material_queue_id",
        "queue_id",
    ),
    "material_review.jsonl": ("material_review_id", "review_id"),
    "fact_ledger_blind.jsonl": ("fact_id",),
    "inference_ledger_blind.jsonl": ("inference_id",),
    "candidate_screening.jsonl": ("screening_id",),
    "candidate_semantic_witness.jsonl": (
        "candidate_semantic_witness_id",
        "witness_id",
        "semantic_witness_id",
    ),
    "candidate_ranking_audit.jsonl": (
        "candidate_ranking_id",
        "ranking_audit_id",
        "source_screening_id",
        "screening_id",
        "candidate_id",
    ),
    "final_evidence_witness.jsonl": ("candidate_id",),
    "final_semantic_audit.jsonl": ("candidate_id",),
    "outcome_ledger.jsonl": ("outcome_row_id", "outcome_id", "outcome_ledger_id"),
    "outcome_leader_census.jsonl": ("outcome_leader_id", "leader_id", "leader_census_id"),
    # The reverse audit's stable row identity survives legacy leader-ID
    # namespace normalization; use it before the mutable relation FK.
    "outcome_to_news_audit.jsonl": (
        "outcome_to_news_audit_id",
        "audit_id",
        "outcome_leader_id",
        "leader_id",
        "leader_census_id",
    ),
    "postmortem_summary.json": ("episode_id",),
    "brain_delta.jsonl": ("record_id", "brain_delta_id", "brain_record_id"),
    "record_provenance_closure_audit.jsonl": (
        "record_id",
        "brain_delta_id",
        "brain_record_id",
    ),
}

_ARTIFACT_ADDITIONAL_UNIQUE_KEYS: dict[str, tuple[str, ...]] = {
    # A source may legitimately have both its ordinary material review and a
    # separately keyed recovered-from-final review.  Queue identity is the
    # queue ID above; source identity is a relation key, not a uniqueness key.
    "material_review.jsonl": (
        "material_review_queue_id",
        "material_queue_id",
        "queue_id",
    ),
    # A screening can have multiple evidence observations.  The witness ID,
    # not screening_id, is the row identity; screening_id is validated as a
    # relation to the screening population below.
    # A single screening can legitimately produce multiple ranked tickers;
    # source_screening_id is a relation key, not a row-identity constraint.
    "candidate_ranking_audit.jsonl": ("candidate_ranking_id", "ranking_audit_id"),
    "outcome_ledger.jsonl": ("ticker", "code"),
    "outcome_leader_census.jsonl": ("outcome_row_id", "outcome_id", "outcome_ledger_id"),
}

_CROSS_BUNDLE_ARTIFACT_KEYS: dict[str, tuple[str, ...]] = {
    "source_ledger.jsonl": ("source_id", "source_row_id", "row_id"),
    "row_disposition.jsonl": ("row_id",),
    "material_review_queue.jsonl": (
        "material_review_queue_id",
        "material_queue_id",
        "queue_id",
    ),
    "material_review.jsonl": ("material_review_id", "review_id"),
    "fact_ledger_blind.jsonl": ("fact_id",),
    "inference_ledger_blind.jsonl": ("inference_id",),
    "candidate_screening.jsonl": ("screening_id",),
    "candidate_semantic_witness.jsonl": ("screening_id", "source_screening_id"),
    "candidate_ranking_audit.jsonl": (
        "candidate_ranking_id",
        "ranking_audit_id",
        "source_screening_id",
        "screening_id",
        "candidate_id",
    ),
    "final_evidence_witness.jsonl": ("candidate_id",),
    "final_semantic_audit.jsonl": ("candidate_id",),
    "outcome_ledger.jsonl": ("outcome_row_id", "outcome_id", "outcome_ledger_id"),
    "outcome_leader_census.jsonl": ("outcome_leader_id", "leader_id", "leader_census_id"),
    "outcome_to_news_audit.jsonl": (
        "outcome_to_news_audit_id",
        "audit_id",
        "outcome_leader_id",
        "leader_id",
        "leader_census_id",
    ),
    "record_provenance_closure_audit.jsonl": (
        "record_id",
        "brain_delta_id",
        "brain_record_id",
    ),
    **_CASE_ID_ALIASES,
}

_CURRENT_PRESEAL_COUNTERS = {
    "preseal_outcome_byte_size_count",
    "preseal_outcome_download_count",
    "preseal_outcome_header_read_count",
    "preseal_outcome_label_calculation_count",
    "preseal_outcome_parse_count",
    "preseal_outcome_row_count_count",
    "preseal_outcome_sample_print_count",
    "preseal_outcome_sha256_count",
    "preseal_outcome_stat_count",
    "preseal_outcome_used_in_blind_graph_count",
    "preseal_outcome_winner_census_count",
}

_SOURCE_ID_FIELDS = ("source_id", "source_row_id", "row_id")
_SOURCE_REFERENCE_ID_PATTERN = re.compile(
    r"(?:SRC-(?:NEWS(?:-ROW)?-)?|NEWS-)(?:\d{8}-)?(\d+)"
)
_RANKABLE_SCREENING_DECISIONS = {"INCLUDE", "WATCH", "WATCH_SECONDARY"}
_LEADER_MEMBERSHIP_FIELDS = (
    "census_inclusion_flags",
    "class_memberships",
    "membership_classes",
    "policy_flags",
    # Legacy leader census rows often put the same machine-readable
    # membership tokens in a field named ``leader_policy_flags`` or encode
    # the class as a scalar ``outcome_class``.  Treat both as evidence fields
    # rather than assuming the canonical list field exists.
    "leader_policy_flags",
    "policy_leader_flags",
    "membership_flags",
    "policy_tags",
    "outcome_class",
    "leader_memberships",
    "leader_policy_memberships",
    "policy_memberships",
    "policy_membership",
    "policy_inclusion_reasons",
    "leader_policy_reasons",
    "leader_reason_flags",
    "leader_policy_tags",
    "leader_basis",
    "leader_criteria",
    "leader_qualifiers",
    "leader_reasons",
    "qualifying_reasons",
    "outcome_classes",
    "outcome_labels",
    "inclusion_flags",
    "inclusion_rules",
    "inclusion_reasons",
    "cohorts",
    "cohort_flags",
    "cohort_memberships",
    "included_by_rule",
    "policy_reasons",
    "policy_criteria",
    "criteria",
    # Some legacy postmortem/leader rows keep the declared universe in a
    # scalar policy expression or a list of criteria.  These are evidence
    # fields, not hard-coded thresholds; the token parser below extracts the
    # numeric policy actually declared by the bundle.
    "selection_policy",
    "selection_criteria_met",
    "outcome_categories",
    "qualifying_classes",
    "reasons",
    "threshold_memberships",
    "membership",
    "census_policy",
    "census_reasons",
    "census_memberships",
    "selection_reasons",
    "cohort_memberships",
    "leader_policy_band",
    # Older outcome/leader artifacts keep the declared policy on each row
    # and store membership cohorts separately.  These are evidence-bearing
    # aliases, not a date- or ticker-specific rule.
    "census_policy",
    "cohort_tags",
    "cohort_policy",
    "policy_bands",
    "leader_policy_version",
    "leader_policy",
    "leader_policies",
    "leader_qualification_rules",
    "outcome_leader_policy",
    "amount_rank_top_group_threshold",
    "turnover_rank_top_group_threshold",
)
_SAFE_PRIOR_CONTEXT_FIELDS = {
    "P_snapshot",
    "p_snapshot",
    "p_snapshot_context",
    "safe_D1_context",
    "safe_D1_context_used",
    "safe_D1_features",
    "safe_d1_context",
    "safe_d1_context_used",
    "safe_d1_features",
}


def evaluate_bundle_quality(
    source_path: Path,
    repaired_path: Path,
    *,
    engine_digest: str,
    deterministic: dict[str, Any] | None = None,
    ephemeral_store: dict[str, Any] | None = None,
    news_csv_root: Path | None = None,
) -> tuple[RepairQualityGate, list[RecordLineageEntry], dict[str, Any]]:
    source_census = census_source(source_path)
    repaired_census = census_source(repaired_path)
    source_rows = artifact_rows(source_path) if source_census.strict_utf8_ok else []
    repaired_rows = artifact_rows(repaired_path) if repaired_census.strict_utf8_ok else []
    source_parsed = parse_generic_bundle(source_path)
    repaired_parsed = parse_generic_bundle(repaired_path)
    repaired_records = repaired_parsed.jsonl_blocks.get("brain_delta.jsonl", [])
    inspection = inspect_versioned_bundle(repaired_path)
    source_ledger_rows = [
        row.row
        for row in source_rows
        if row.canonical_name == "source_ledger.jsonl"
    ]
    repaired_source_ledger_rows = [
        row.row
        for row in repaired_rows
        if row.canonical_name == "source_ledger.jsonl"
    ]
    timestamp_repair, verified_source_timestamps = (
        audit_rehydrated_news_source_timestamps(
            source_ledger_rows,
            repaired_source_ledger_rows,
            news_csv_root=news_csv_root,
            cutoff_at=_bundle_cutoff(_rows_by_name(repaired_rows)),
            declared_input_file=_first(
                source_parsed.front_matter,
                "input_file",
            )
            or _first(
                source_parsed.json_blocks.get("input_audit.json", {}),
                "input_file",
            ),
            declared_input_sha256=_first(
                source_parsed.front_matter,
                "input_sha256",
            )
            or _first(
                source_parsed.json_blocks.get("input_audit.json", {}),
                "input_sha256",
            ),
        )
    )
    lineage, lineage_audit = _build_lineage(
        source_census,
        source_rows,
        repaired_records,
    )
    lineage_audit.update(
        _artifact_lineage_audit(
            source_rows,
            repaired_rows,
            verified_source_timestamps=verified_source_timestamps,
        )
    )
    lineage_audit.update(_derived_case_population_audit(source_rows, repaired_rows))
    lineage_audit.update(_artifact_occurrence_lineage_audit(source_census, repaired_census))
    source_population = _population_audit(
        source_rows,
        present_artifact_names=set(source_census.artifact_counts),
    )
    repaired_population = _population_audit(
        repaired_rows,
        present_artifact_names=set(repaired_census.artifact_counts),
    )
    population = _combined_population(source_population, repaired_population)
    source_semantic = _semantic_audit(source_rows)
    repaired_semantic = _semantic_audit(repaired_rows)
    resolved_source_semantic_failures = _resolved_source_semantic_failures(
        source_rows,
        repaired_rows,
        source_failures=set(source_semantic["failures"]),
    )
    semantic = {
        "source": source_semantic,
        "repaired": repaired_semantic,
        "source_failure_resolved_by_repair_count": len(resolved_source_semantic_failures),
        "source_failure_resolved_by_repair": sorted(resolved_source_semantic_failures),
        "failure_count": (
            source_semantic["failure_count"]
            + repaired_semantic["failure_count"]
            - len(resolved_source_semantic_failures)
        ),
        "current_regression_contract_pass": repaired_semantic["current_regression_contract_pass"],
    }
    temporal = {
        "status": "NOT_EVALUATED_IN_REPAIR_ONLY_MODE",
        "failure_count": 0,
        "blind_outcome_leak_count": 0,
        "ready_failure_count": 0,
    }
    provenance, eligibility = _provenance_and_eligibility_audit(
        repaired_rows,
        repaired_records,
        lineage,
    )
    eligibility.update(_semantic_exclusion_audit(repaired_rows, repaired_records))
    provenance.update(timestamp_repair)
    importer = _importer_audit(inspection)
    source_parse_issue_count = _artifact_parse_issue_count(source_census)
    repaired_parse_issue_count = _artifact_parse_issue_count(repaired_census)
    source_record_type_reconciliation = _record_type_token_reconciliation(
        source_census,
        source_rows,
    )
    repaired_record_type_reconciliation = _record_type_token_reconciliation(
        repaired_census,
        repaired_rows,
    )
    replacement_character_preserved = (
        source_census.replacement_character_count
        == repaired_census.replacement_character_count
        and source_census.replacement_character_count > 0
    )
    raw_census = {
        "strict_utf8_ok": source_census.strict_utf8_ok,
        "replacement_character_count": source_census.replacement_character_count,
        "artifact_occurrence_count": len(source_census.artifact_occurrences),
        "unclaimed_machine_payload_count": len(source_census.unclaimed_machine_payloads),
        "conflicting_duplicate_block_count": len(source_census.conflicting_duplicate_names),
        "duplicate_block_name_count": len(source_census.duplicate_names),
        "source_explicit_record_count": source_census.explicit_record_count,
        "source_raw_record_type_token_count": (source_census.raw_record_type_token_count),
        "source_claimed_record_type_token_count": source_record_type_reconciliation["claimed_token_count"],
        "source_unreconciled_record_type_token_count": (source_record_type_reconciliation["unreconciled_token_count"]),
        "source_artifact_parse_issue_count": source_parse_issue_count,
        "repaired_strict_utf8_ok": repaired_census.strict_utf8_ok,
        "repaired_replacement_character_count": (repaired_census.replacement_character_count),
        "repaired_unclaimed_machine_payload_count": len(repaired_census.unclaimed_machine_payloads),
        "repaired_conflicting_duplicate_block_count": len(repaired_census.conflicting_duplicate_names),
        "repaired_duplicate_block_name_count": len(repaired_census.duplicate_names),
        "repaired_explicit_record_count": repaired_census.explicit_record_count,
        "repaired_raw_record_type_token_count": (repaired_census.raw_record_type_token_count),
        "repaired_claimed_record_type_token_count": (repaired_record_type_reconciliation["claimed_token_count"]),
        "repaired_unreconciled_record_type_token_count": (
            repaired_record_type_reconciliation["unreconciled_token_count"]
        ),
        "repaired_artifact_parse_issue_count": repaired_parse_issue_count,
        "replacement_character_preserved": replacement_character_preserved,
    }
    deterministic_result = dict(deterministic or {})
    ephemeral_result = dict(ephemeral_store or {})
    duplicate = {
        "duplicate_names": source_census.duplicate_names,
        "conflicting_duplicate_names": source_census.conflicting_duplicate_names,
    }

    importer_compatible = all(
        (
            importer["validation_passed"],
            importer["import_loss_audit_passed"],
            importer["missing_normalized_record_count"] == 0,
            importer["extra_normalized_record_count"] == 0,
            importer["raw_normalized_record_count_matches"] is True,
            importer["training_eligible_count_matches_raw"] is True,
            importer["quarantined_record_count"] == 0,
            importer["missing_source_reference_count"] == 0,
            importer["missing_payload_reference_count"] == 0,
            importer["invalid_typed_payload_record_count"] == 0,
        )
    )
    raw_gate_without_replacement = all(
        (
            source_census.strict_utf8_ok,
            not source_census.unclaimed_machine_payloads,
            not source_census.duplicate_names,
            not source_census.conflicting_duplicate_names,
            source_parse_issue_count == 0,
            source_record_type_reconciliation["unreconciled_token_count"] == 0,
            repaired_census.strict_utf8_ok,
            not repaired_census.unclaimed_machine_payloads,
            not repaired_census.duplicate_names,
            not repaired_census.conflicting_duplicate_names,
            repaired_parse_issue_count == 0,
            repaired_record_type_reconciliation["unreconciled_token_count"] == 0,
        )
    )
    raw_gate_passed = raw_gate_without_replacement and all(
        (
            source_census.replacement_character_count == 0,
            repaired_census.replacement_character_count == 0,
        )
    )
    # A replacement character already present in the source bytes cannot be
    # safely reconstructed by repair.  If the exact count survives unchanged,
    # keep the source as importable legacy material with an explicit warning;
    # strict current-gold still requires zero replacement characters.
    raw_gate_importable = raw_gate_without_replacement and (
        raw_gate_passed or replacement_character_preserved
    )
    importable_legacy = all(
        (
            # An empty brain_delta is not an importable research result even
            # when every empty-set parity check happens to agree.  Empty
            # payloads are handled by the source classifier (for example,
            # non-trading days); a discovered source with no records must be
            # preserved as source-payload-absent instead of becoming a false
            # PASS.
            source_census.explicit_record_count > 0,
            importer_compatible,
            raw_gate_importable,
            lineage_audit["unaccounted_original_record_count"] == 0,
            lineage_audit["orphan_repaired_record_count"] == 0,
            lineage_audit["cross_record_ref_missing_count"] == 0,
            lineage_audit["illegal_transform_count"] == 0,
            lineage_audit["artifact_missing_source_row_count"] == 0,
            lineage_audit["artifact_orphan_repaired_row_count"] == 0,
            lineage_audit["artifact_illegal_transform_count"] == 0,
            lineage_audit.get(
                "derived_case_link_effective_failure_count",
                lineage_audit["derived_case_link_failure_count"],
            )
            == 0,
            lineage_audit["artifact_occurrence_missing_count"] == 0,
            lineage_audit["artifact_occurrence_changed_count"] == 0,
            lineage_audit["artifact_occurrence_orphan_count"] == 0,
            (
                population["legacy_contract_population_quarantine"]
                or population["population_underfill_count"] == 0
            ),
            (
                population["legacy_contract_population_quarantine"]
                or population["population_extra_count"] == 0
            ),
            population["duplicate_logical_key_count"] == 0,
            (
                population["legacy_contract_population_quarantine"]
                or population["liquidity_policy_underspecified_count"] == 0
            ),
            provenance["eligible_empty_source_count"] == 0,
            provenance["eligible_placeholder_only_count"] == 0,
            provenance["eligible_unresolved_source_count"] == 0,
            provenance["eligible_time_unverified_source_count"] == 0,
            provenance["closure_content_mismatch_count"] == 0,
            provenance["timestamp_repair_failure_count"] == 0,
            eligibility["false_to_true_count"] == 0,
            eligibility["ineligible_nonzero_weight_count"] == 0,
            eligibility["ineligible_missing_reason_count"] == 0,
            eligibility["semantic_invalid_training_eligible_count"] == 0,
            deterministic_result.get("matches") is True,
            ephemeral_result.get("passed") is True,
        )
    )
    mechanical_gold_ready = all(
        (
            importable_legacy,
            raw_gate_passed,
            population["current_contract_blocks_present"] is True,
        )
    )
    current_gold_pass = all(
        (
            mechanical_gold_ready,
            ephemeral_result.get("real_store_unchanged") is True,
        )
    )
    ready_for_import_pass = all(
        (
            importable_legacy,
            ephemeral_result.get("real_store_unchanged") is True,
        )
    )
    blockers = _quality_blockers(
        raw_census=raw_census,
        lineage=lineage_audit,
        population=population,
        importer=importer,
        provenance=provenance,
        eligibility=eligibility,
        semantic=semantic,
        temporal=temporal,
        deterministic=deterministic_result,
        ephemeral_store=ephemeral_result,
    )
    if source_census.explicit_record_count == 0:
        blockers.append("SOURCE_PAYLOAD_ABSENT:brain_delta_record_count=0")
    warnings: list[str] = []
    if eligibility["semantic_excluded_record_count"]:
        warnings.append("SEMANTIC_RECORDS_EXCLUDED_FROM_TRAINING")
    if population["legacy_contract_population_quarantine"]:
        warnings.append("LEGACY_CONTRACT_POPULATION_QUARANTINED")
    if provenance.get("closure_artifact_absent_eligible_count", 0):
        warnings.append("LEGACY_CLOSURE_ARTIFACT_ABSENT_RECOMPUTED")
    if raw_census.get("replacement_character_preserved") is True:
        warnings.append("SOURCE_ENCODING_REPLACEMENT_PRESERVED")
    if provenance.get("eligible_placeholder_reference_count", 0):
        warnings.append("PROVENANCE_PLACEHOLDER_REFERENCE_PRESERVED")

    if ready_for_import_pass:
        final_status = RepairTaskState.REPAIRED_PASS
    elif source_census.explicit_record_count > 0:
        final_status = RepairTaskState.PRESERVED_PARTIAL_NOT_CURRENT_GOLD
    else:
        final_status = RepairTaskState.PRESERVED_SOURCE_PAYLOAD_ABSENT
    gate = RepairQualityGate(
        source_sha256=source_census.source_sha256,
        repaired_sha256=repaired_census.source_sha256,
        repaired_byte_size=repaired_census.byte_size,
        engine_digest=engine_digest,
        passed=ready_for_import_pass,
        ready_for_import_pass=ready_for_import_pass,
        importable_legacy=importable_legacy,
        current_gold_pass=current_gold_pass,
        mechanical_gold_ready=mechanical_gold_ready,
        final_status=final_status,
        blockers=blockers,
        warnings=warnings,
        raw_census=raw_census,
        lineage=lineage_audit,
        population=population,
        importer=importer,
        provenance=provenance,
        eligibility=eligibility,
        temporal=temporal,
        semantic=semantic,
        duplicate=duplicate,
        deterministic=deterministic_result,
    )
    auxiliary = {
        "source_census": source_census.model_dump(mode="json"),
        "repaired_census": repaired_census.model_dump(mode="json"),
        "ephemeral_store": ephemeral_result,
    }
    return gate, lineage, auxiliary


def _build_lineage(
    source_census: SourceCensus,
    source_rows: list[ArtifactRow],
    repaired_records: list[dict[str, Any]],
) -> tuple[list[RecordLineageEntry], dict[str, Any]]:
    original_rows = [row for row in source_rows if row.canonical_name == "brain_delta.jsonl"]
    repaired_by_original_id: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    repaired_by_id: dict[str, dict[str, Any]] = {}
    for index, repaired_record in enumerate(repaired_records):
        record_id = _record_id(repaired_record)
        if record_id is None:
            continue
        repaired_by_id[record_id] = repaired_record
        for identity in _record_identity_values(repaired_record):
            repaired_by_original_id[identity].append((index, repaired_record))

    lineages: list[RecordLineageEntry] = []
    matched_repaired_indices: set[int] = set()
    unaccounted = 0
    illegal_transform_count = 0
    cross_ref_missing = 0
    source_ids = {_record_id(row.row) for row in original_rows if _record_id(row.row)}
    source_to_repaired_id: dict[str, str] = {}
    for row in original_rows:
        original = row.row
        original_id = _record_id(original)
        candidates = [
            candidate
            for identity in _record_identity_values(original)
            for candidate in repaired_by_original_id.get(identity, [])
        ]
        unique_candidates = dict(candidates)
        selected: tuple[int, dict[str, Any]] | None = None
        if len(unique_candidates) == 1:
            selected = next(iter(unique_candidates.items()))
        if selected is None:
            unaccounted += 1
            repaired_index = None
            matched_record: dict[str, Any] | None = None
        else:
            repaired_index, matched_record = selected
            matched_repaired_indices.add(repaired_index)
            if original_id is not None and _record_id(matched_record) is not None:
                source_to_repaired_id[original_id] = _record_id(matched_record) or original_id
        changed_fields = _changed_fields(original, matched_record or {})
        transform_rules = _transform_rules(original, matched_record)
        illegal_paths = _illegal_transform_paths(original, matched_record)
        if illegal_paths:
            illegal_transform_count += 1
            changed_fields["illegal_transform_paths"] = illegal_paths
        lineages.append(
            RecordLineageEntry(
                origin_key=row.origin_key,
                source_sha256=source_census.source_sha256,
                artifact_occurrence_id=row.occurrence_id,
                row_ordinal=row.row_ordinal,
                raw_payload_sha256=row.raw_payload_sha256,
                raw_row_bytes_sha256=row.raw_row_bytes_sha256,
                original_domain_id=original_id,
                original_record_type=_string(original.get("record_type")),
                repaired_record_id=_record_id(matched_record or {}),
                repaired_record_type=_string((matched_record or {}).get("record_type")),
                repaired_payload_sha256=(
                    sha256_text(canonical_json(matched_record)) if matched_record is not None else None
                ),
                lineage_kind="EXPLICIT",
                status=(
                    "ILLEGAL_TRANSFORM"
                    if illegal_paths
                    else "PRESERVED"
                    if matched_record is not None
                    else "UNACCOUNTED"
                ),
                transform_rule_ids=transform_rules,
                changed_fields=changed_fields,
                training_eligible_before=_bool_or_none(original.get("training_eligible")),
                training_eligible_after=_bool_or_none((matched_record or {}).get("training_eligible")),
                eligibility_transition_reason=_string((matched_record or {}).get("training_exclusion_reason")),
                provenance_before=_string_list(original.get("provenance_source_ids")),
                provenance_after=_string_list((matched_record or {}).get("provenance_source_ids")),
            )
        )

    derived_count = 0
    derived_origins = _derived_origin_index(source_rows)
    for repaired_index, repaired_record in enumerate(repaired_records):
        if repaired_index in matched_repaired_indices:
            continue
        derived_origin = _derived_origin_for_record(repaired_record, derived_origins)
        if derived_origin is None:
            continue
        matched_repaired_indices.add(repaired_index)
        derived_count += 1
        lineages.append(
            RecordLineageEntry(
                origin_key=derived_origin.origin_key,
                source_sha256=source_census.source_sha256,
                artifact_occurrence_id=derived_origin.occurrence_id,
                row_ordinal=derived_origin.row_ordinal,
                raw_payload_sha256=derived_origin.raw_payload_sha256,
                raw_row_bytes_sha256=derived_origin.raw_row_bytes_sha256,
                original_domain_id=_first(
                    derived_origin.row,
                    *_case_identifier_fields(),
                ),
                original_record_type=derived_origin.canonical_name,
                repaired_record_id=_record_id(repaired_record),
                repaired_record_type=_string(repaired_record.get("record_type")),
                repaired_payload_sha256=sha256_text(canonical_json(repaired_record)),
                lineage_kind="DERIVED",
                status="DERIVED",
                transform_rule_ids=["DERIVED_FROM_EXPLICIT_CASE_ARTIFACT_V1"],
                derivation_inputs=[derived_origin.origin_key],
                training_eligible_after=_bool_or_none(repaired_record.get("training_eligible")),
                eligibility_transition_reason=_string(repaired_record.get("training_exclusion_reason")),
                provenance_after=_string_list(repaired_record.get("provenance_source_ids")),
            )
        )

    for lineage, row in zip(
        lineages[: len(original_rows)],
        original_rows,
        strict=True,
    ):
        if lineage.repaired_record_id is None:
            continue
        lineage_record = repaired_by_id.get(lineage.repaired_record_id)
        if lineage_record is None:
            continue
        repaired_reference_values = _record_references(lineage_record) | _record_identity_values(
            lineage_record
        )
        for reference in _record_references(row.row):
            if reference not in source_ids:
                continue
            expected = source_to_repaired_id.get(reference)
            if expected is not None and expected not in repaired_reference_values:
                cross_ref_missing += 1

    orphan_repaired = len(repaired_records) - len(matched_repaired_indices)
    false_to_true = sum(
        1
        for lineage in lineages
        if lineage.lineage_kind == "EXPLICIT"
        and lineage.training_eligible_before is not True
        and lineage.training_eligible_after is True
    )
    return lineages, {
        "original_explicit_record_count": len(original_rows),
        "matched_original_record_count": len(original_rows) - unaccounted,
        "legally_preserved_original_record_count": (len(original_rows) - unaccounted - illegal_transform_count),
        "derived_record_count": derived_count,
        "unaccounted_original_record_count": unaccounted,
        "orphan_repaired_record_count": max(0, orphan_repaired),
        "illegal_transform_count": illegal_transform_count,
        "cross_record_ref_missing_count": cross_ref_missing,
        "false_to_true_count": false_to_true,
    }


def _provenance_identifier_alias_equivalent(old: Any, new: Any) -> bool:
    """Return whether a legacy row/source spelling maps to the same source.

    Some bundles already carry the canonical ``SRC-`` ID at the record level
    while repeating the CSV row alias (``ROW-``) inside the payload.  Repair
    may normalize that nested mirror, but only for the same numeric row and
    only for the explicitly known row-to-source spellings.
    """

    if not isinstance(old, str) or not isinstance(new, str):
        return False
    if old == new:
        return True
    patterns = (
        (r"ROW-(\d+)", r"SRC-(\d+)"),
        (r"NEWS-ROW-(\d+)", r"SRC-(\d+)"),
        (r"SRC-NEWS-ROW-(\d+)", r"SRC-(\d+)"),
    )
    return any(
        (old_match := re.fullmatch(old_pattern, old)) is not None
        and (new_match := re.fullmatch(new_pattern, new)) is not None
        and old_match.group(1) == new_match.group(1)
        for old_pattern, new_pattern in patterns
    )


def _provenance_alias_list_change_allowed(
    before: dict[str, Any],
    after: dict[str, Any],
    old: list[Any],
    new: list[Any],
) -> bool:
    """Allow only source-ledger-anchored nested provenance normalization."""

    before_top = _string_list(before.get("provenance_source_ids"))
    after_top = _string_list(after.get("provenance_source_ids"))
    old_values = _string_list(old)
    new_values = _string_list(new)
    if not before_top or not after_top or not old_values or not new_values:
        return False
    # The canonical record-level provenance set itself must not change.  This
    # prevents this narrow alias exception from approving source invention or
    # source removal elsewhere in the record.
    if Counter(before_top) != Counter(after_top):
        return False
    if Counter(new_values) != Counter(after_top):
        return False
    if len(old_values) != len(new_values):
        return False
    unmatched = list(new_values)
    for old_value in old_values:
        candidates = [
            value
            for value in unmatched
            if _provenance_identifier_alias_equivalent(old_value, value)
        ]
        if len(candidates) != 1:
            return False
        unmatched.remove(candidates[0])
    return not unmatched


def _artifact_lineage_audit(
    source_rows: list[ArtifactRow],
    repaired_rows: list[ArtifactRow],
    *,
    verified_source_timestamps: dict[str, str] | None = None,
) -> dict[str, Any]:
    source_by_name = _rows_by_name(source_rows)
    repaired_by_name = _rows_by_name(repaired_rows)
    missing: list[str] = []
    orphan: list[str] = []
    illegal: list[str] = []
    derived_placeholder_count = 0
    for block_name, key_fields in _CROSS_BUNDLE_ARTIFACT_KEYS.items():
        source_index = _unique_artifact_row_index(
            source_by_name.get(block_name, []),
            key_fields,
        )
        repaired_index = _unique_artifact_row_index(
            repaired_by_name.get(block_name, []),
            key_fields,
        )
        for key, source_row in source_index.items():
            repaired_row = repaired_index.get(key)
            if repaired_row is None:
                missing.append(f"{block_name}:{key}")
                continue
            known_transform = (
                block_name == "record_provenance_closure_audit.jsonl"
                or (
                    block_name == "candidate_semantic_witness.jsonl"
                    and _candidate_semantic_alias_transform_valid(
                        source_row.row,
                        repaired_row.row,
                        repaired_by_name=repaired_by_name,
                    )
                )
                or (
                    block_name
                    in {
                        "candidate_semantic_witness.jsonl",
                        "final_evidence_witness.jsonl",
                    }
                    and _semantic_primary_fact_transform_valid(
                        source_row.row,
                        repaired_row.row,
                        repaired_by_name=repaired_by_name,
                    )
                )
            )
            changed: list[str] = (
                []
                if known_transform
                else _illegal_transform_paths(
                    source_row.row,
                    repaired_row.row,
                    artifact_name=block_name,
                    verified_source_timestamp=(
                        (verified_source_timestamps or {}).get(key)
                        if block_name == "source_ledger.jsonl"
                        else None
                    ),
                )
            )
            if changed:
                illegal.append(f"{block_name}:{key}:{','.join(changed[:5])}")
        for key, repaired_row in repaired_index.items():
            if key in source_index:
                continue
            if (
                block_name == "source_ledger.jsonl"
                and _is_repair_placeholder_source(repaired_row.row)
            ) or (
                block_name == "record_provenance_closure_audit.jsonl"
                and repaired_row.row.get("repair_generated_for_derived_record") is True
            ) or (
                block_name == "material_review.jsonl"
                and repaired_row.row.get("repair_derived_from_queue") is True
            ) or (
                block_name == "candidate_semantic_witness.jsonl"
                and _derived_candidate_semantic_witness_valid(
                    repaired_row.row,
                    source_by_name=source_by_name,
                )
            ):
                derived_placeholder_count += 1
            else:
                orphan.append(f"{block_name}:{key}")
    return {
        "artifact_missing_source_row_count": len(missing),
        "artifact_orphan_repaired_row_count": len(orphan),
        "artifact_illegal_transform_count": len(illegal),
        "artifact_derived_placeholder_count": derived_placeholder_count,
        "artifact_missing_source_row_samples": missing[:50],
        "artifact_orphan_repaired_row_samples": orphan[:50],
        "artifact_illegal_transform_samples": illegal[:50],
    }


def _derived_candidate_semantic_witness_valid(
    repaired: dict[str, Any],
    *,
    source_by_name: dict[str, list[ArtifactRow]],
) -> bool:
    """Rebuild a repair-added witness from the source's exact rankable chain."""

    def unique_index(
        name: str,
        *fields: str,
    ) -> dict[str, dict[str, Any]]:
        candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for artifact_row in source_by_name.get(name, []):
            for field in fields:
                value = _string(artifact_row.row.get(field))
                if value is not None:
                    candidates[value].append(artifact_row.row)
        return {
            value: matches[0]
            for value, matches in candidates.items()
            if len(matches) == 1
        }

    screenings = unique_index("candidate_screening.jsonl", "screening_id")
    rankings = unique_index(
        "candidate_ranking_audit.jsonl",
        "source_screening_id",
        "screening_id",
    )
    facts = unique_index("fact_ledger_blind.jsonl", "fact_id")
    inferences = unique_index("inference_ledger_blind.jsonl", "inference_id")
    reviews = unique_index(
        "material_review.jsonl",
        "material_review_id",
        "review_id",
    )
    sources = unique_index(
        "source_ledger.jsonl",
        "source_id",
        "source_row_id",
        "row_id",
    )

    def modern_expected_values(screening_id: str) -> dict[str, Any] | None:
        screening = screenings.get(screening_id)
        ranking = rankings.get(screening_id)
        if screening is None or ranking is None:
            return None
        decision = str(screening.get("screening_decision") or "").upper()
        if (
            decision not in _RANKABLE_SCREENING_DECISIONS
            and screening.get("rankable") is not True
        ):
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
        source_id = _first(fact, "source_row_id", "source_id")
        source = sources.get(source_id or "")
        exact_quote = _first(fact, "exact_quote")
        semantic_witness = _first(screening, "decision_reason_specific")
        candidate_id = _first(screening, "candidate_id")
        company = _first(screening, "company", "candidate_company")
        ticker = _first(screening, "ticker", "code")
        inference_text = _first(inference, "mechanism_sentence", "statement")
        if (
            source is None
            or exact_quote is None
            or semantic_witness is None
            or candidate_id is None
            or company is None
            or ticker is None
            or inference_text != semantic_witness
            or _first(ranking, "source_screening_id", "screening_id")
            != screening_id
            or _first(ranking, "candidate_id") != candidate_id
            or _first(ranking, "ticker", "code") != ticker
            or _first(ranking, "company", "candidate_company") != company
            or _string_list(
                inference.get("source_fact_ids")
                or inference.get("supporting_fact_ids")
            )
            != fact_ids
            or inference.get("mechanism_supported") is not True
            or _first(review, "source_id", "source_row_id") != source_id
            or _first(review, "exact_quote") != exact_quote
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
            "source_phase": _first(screening, "source_phase") or "BLIND",
            "ticker": ticker,
        }

    def legacy_expected_values(screening_id: str) -> dict[str, Any] | None:
        screening = screenings.get(screening_id)
        ranking = rankings.get(screening_id)
        if screening is None or ranking is None:
            return None
        decision = str(screening.get("screening_decision") or "").upper()
        if (
            decision not in _RANKABLE_SCREENING_DECISIONS
            and screening.get("rankable") is not True
        ):
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
        source_id = _first(fact, "source_row_id", "source_id")
        source = sources.get(source_id or "")
        exact_quote = _first(fact, "exact_quote")
        candidate_id = _first(screening, "candidate_id")
        company = _first(screening, "company", "candidate_company")
        ticker = _first(screening, "ticker", "code")
        inference_text = _first(inference, "mechanism_sentence", "statement")
        decision_reason = _first(screening, "decision_reason_specific")
        fact_class = _first(fact, "fact_class")
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
            or _first(ranking, "source_screening_id", "screening_id")
            != screening_id
            or _first(ranking, "candidate_id") != candidate_id
            or _first(ranking, "ticker", "code") != ticker
            or _first(ranking, "company", "candidate_company") != company
            or _string_list(
                inference.get("source_fact_ids")
                or inference.get("supporting_fact_ids")
            )
            != fact_ids
            or inference.get("mechanism_supported") is not True
            or _first(review, "source_id", "source_row_id") != source_id
            or _first(review, "exact_quote") != exact_quote
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
            "source_phase": _first(screening, "source_phase") or "BLIND",
            "source_screening_id": screening_id,
        }

    # Existing source rows prove which source fields this bundle family uses
    # for its witness representation. Refuse a derivation if that convention
    # is absent or internally inconsistent.
    source_witnesses = source_by_name.get("candidate_semantic_witness.jsonl", [])
    if not source_witnesses:
        return False
    expected_values = modern_expected_values
    for candidate_builder in (modern_expected_values, legacy_expected_values):
        if all(
            (expected := candidate_builder(
                _first(
                    artifact_row.row,
                    "screening_id",
                    "source_screening_id",
                )
                or ""
            ))
            is not None
            and all(
                artifact_row.row.get(field) == value
                for field, value in expected.items()
            )
            for artifact_row in source_witnesses
        ):
            expected_values = candidate_builder
            break
    else:
        return False

    screening_id = _first(repaired, "screening_id", "source_screening_id")
    expected = expected_values(screening_id or "")
    if expected is None:
        return False
    screening = screenings[screening_id or ""]
    ranking = rankings[screening_id or ""]
    fact_id = _string_list(screening.get("source_fact_ids"))[0]
    inference_id = _string_list(screening.get("source_inference_ids"))[0]
    review_id = _string_list(
        screening.get("source_material_review_ids")
        or screening.get("material_review_ids")
    )[0]
    fact = facts[fact_id]
    inference = inferences[inference_id]
    review = reviews[review_id]
    source_id = _first(fact, "source_row_id", "source_id")
    if source_id is None:
        return False
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
    return repaired == expected


def _derived_case_population_audit(
    source_rows: list[ArtifactRow],
    repaired_rows: list[ArtifactRow],
) -> dict[str, Any]:
    source_by_name = _rows_by_name(source_rows)
    repaired_by_name = _rows_by_name(repaired_rows)
    source_brain_by_alias: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in source_by_name.get("brain_delta.jsonl", []):
        for identity in _record_identity_values(row.row):
            source_brain_by_alias[identity].append(row.row)
    # Case derivations repeat the same case->brain join for every repaired row.
    # Build that deterministic join once per source case; otherwise a large
    # bundle with hundreds of derived rows becomes O(cases * records) per row.
    population_match_cache = _case_population_match_cache(source_by_name)
    failures: list[str] = []
    source_gap_failures: list[str] = []
    validated = 0
    for repaired_row in repaired_by_name.get("brain_delta.jsonl", []):
        repaired = repaired_row.row
        derivations = repaired.get("repair_population_derivations")
        if not isinstance(derivations, list):
            continue
        repaired_id = _record_id(repaired)
        source_candidates = {
            source_id: candidate
            for identity in _record_identity_values(repaired)
            for candidate in source_brain_by_alias.get(identity, [])
            for source_id in [_record_id(candidate)]
            if source_id is not None
        }
        source = (
            next(iter(source_candidates.values()))
            if len(source_candidates) == 1
            else None
        )
        for derivation in derivations:
            reason = _validate_case_population_derivation(
                derivation,
                source_record=source,
                repaired_record=repaired,
                source_by_name=source_by_name,
                population_match_cache=population_match_cache,
            )
            if reason is None:
                validated += 1
            else:
                failure_key = f"{repaired_id or repaired_row.origin_key}:{reason}"
                failures.append(failure_key)
                if reason == "split_case_fact_coverage_incomplete" and _case_population_source_gap(
                    derivation,
                    source_by_name=source_by_name,
                ):
                    source_gap_failures.append(failure_key)
    return {
        "derived_case_link_count": validated,
        "derived_case_link_failure_count": len(failures),
        "derived_case_link_failure_samples": failures[:50],
        "derived_case_link_source_gap_count": len(source_gap_failures),
        "derived_case_link_source_gap_samples": source_gap_failures[:50],
        "derived_case_link_effective_failure_count": len(failures) - len(source_gap_failures),
    }


def _case_population_source_gap(
    derivation: Any,
    *,
    source_by_name: dict[str, list[ArtifactRow]],
) -> bool:
    """Detect a case fact that has no corresponding source brain row.

    This is a source-population gap, not a repair loss. It is safe to classify
    as a legacy warning only when no source brain record carries the missing
    fact; if such a record exists, the normal derived-link hard gate remains.
    """
    if not isinstance(derivation, dict):
        return False
    block_name = _string(derivation.get("source_artifact"))
    case_id = _string(derivation.get("source_case_id"))
    if block_name is None or case_id is None:
        return False
    aliases = _CASE_ID_ALIASES.get(block_name, ())
    cases = [
        row.row
        for row in source_by_name.get(block_name, [])
        if case_id in _field_string_values(row.row, *aliases)
    ]
    if len(cases) != 1:
        return False
    case = cases[0]
    case_facts = _case_relation_values(
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
    if not case_facts:
        return False
    represented: set[str] = set()
    any_fact_occurrence: set[str] = set()
    for artifact_row in source_by_name.get("brain_delta.jsonl", []):
        row = artifact_row.row
        row_facts = _case_relation_values(
            row,
            "source_fact_ids",
            "fact_ids",
            "blind_fact_ids",
            "sealed_fact_ids",
            "blind_selected_fact_ids",
            "selected_fact_ids",
        )
        if not row_facts:
            continue
        case_ticker = _first(case, "ticker", "candidate_ticker")
        row_ticker = _case_population_row_ticker(
            row,
            source_by_name=source_by_name,
        )
        case_date = _string(case.get("trade_date"))
        row_date = _string(row.get("trade_date"))
        if case_ticker and row_ticker != case_ticker:
            continue
        if case_date and row_date and case_date != row_date:
            continue
        # A fact occurrence on another ticker/date is not evidence that this
        # aggregate's missing lane is represented.  Count only occurrences in
        # the same case identity before deciding whether the source has a
        # population gap.
        any_fact_occurrence.update(row_facts)
        if not row_facts.issubset(case_facts):
            continue
        represented.update(row_facts)
    missing = case_facts - represented
    return bool(missing) and missing.isdisjoint(any_fact_occurrence)


def _case_population_row_ticker(
    row: dict[str, Any],
    *,
    source_by_name: dict[str, list[ArtifactRow]],
) -> str | None:
    explicit = _case_relation_values(row, "ticker", "candidate_ticker")
    if len(explicit) == 1:
        return next(iter(explicit))
    leader_ids = _case_relation_values(
        row,
        "outcome_leader_id",
        "outcome_leader_ids",
        "leader_id",
        "leader_ids",
        "leader_census_id",
        "leader_census_ids",
    )
    if not leader_ids:
        return None
    linked_tickers = {
        ticker
        for leader_row in source_by_name.get("outcome_leader_census.jsonl", [])
        if leader_ids
        & _field_string_values(
            leader_row.row,
            "outcome_leader_id",
            "leader_id",
            "leader_census_id",
        )
        for ticker in [_outcome_ticker(leader_row.row)]
        if ticker is not None
    }
    return next(iter(linked_tickers)) if len(linked_tickers) == 1 else None


def _case_population_match_cache(
    source_by_name: dict[str, list[ArtifactRow]],
) -> dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]] | None]:
    cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]] | None] = {}
    source_brain_rows = [row.row for row in source_by_name.get("brain_delta.jsonl", [])]
    record_index = _case_population_record_index(source_brain_rows)
    for block_name, (canonical_record_type, target_field) in _DERIVED_CASE_TARGETS.items():
        aliases = _CASE_ID_ALIASES.get(block_name, (target_field,))
        for artifact_row in source_by_name.get(block_name, []):
            case_ids = _field_string_values(artifact_row.row, *aliases)
            if not case_ids:
                continue
            match = _best_case_population_match(
                block_name,
                artifact_row.row,
                canonical_record_type=canonical_record_type,
                records=_indexed_case_population_candidates(
                    artifact_row.row,
                    records=source_brain_rows,
                    record_index=record_index,
                ),
            )
            # A legacy row can expose more than one name for the same case
            # (for example ``blind_pair_id`` and ``case_id``).  Cache the
            # join under every observed alias so a derivation that chose any
            # one of those names does not become an artificial miss.
            for case_id in case_ids:
                cache[
                    (
                        block_name,
                        _case_population_cache_id(case_id, artifact_row.row),
                    )
                ] = match
    return cache


_CASE_POPULATION_INDEX_FIELDS: dict[str, tuple[str, ...]] = {
    "outcome_leader_id": ("outcome_leader_id",),
    "candidate_id": ("candidate_id", "candidate_ids"),
    "theme_id": (
        "theme_id",
        "theme_ids",
        "theme_case_id",
        "theme_case_ids",
        "theme",
        "theme_key",
        "theme_keys",
    ),
    "source_screening_id": (
        "source_screening_id",
        "source_screening_ids",
        "screening_id",
        "screening_ids",
    ),
    "outcome_audit_id": (
        "audit_id",
        "audit_ids",
        "outcome_audit_id",
        "outcome_audit_ids",
    ),
    "fact_id": (
        "matched_fact_ids",
        "sealed_fact_ids",
        "sealed_source_fact_ids",
        "source_fact_ids",
        "combined_fact_ids",
        "fact_ids",
        "blind_fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    ),
}


def _case_population_index_keys(row: dict[str, Any]) -> set[tuple[str, str]]:
    keys = {
        (relation, _relation_alias_key(value))
        for relation, fields in _CASE_POPULATION_INDEX_FIELDS.items()
        for value in _case_relation_values(row, *fields)
    }
    ticker = _first(row, "ticker", "candidate_ticker")
    if ticker is not None:
        keys.add(("ticker", ticker))
    return keys


def _case_population_record_index(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], list[int]]:
    index: dict[tuple[str, str], list[int]] = defaultdict(list)
    for position, record in enumerate(records):
        for key in _case_population_index_keys(record):
            index[key].append(position)
    return index


def _indexed_case_population_candidates(
    case: dict[str, Any],
    *,
    records: list[dict[str, Any]],
    record_index: dict[tuple[str, str], list[int]],
) -> list[dict[str, Any]]:
    positions = {
        position
        for key in _case_population_index_keys(case)
        for position in record_index.get(key, [])
    }
    return [records[position] for position in sorted(positions)]


def _case_population_cache_id(case_id: str, case: dict[str, Any]) -> str:
    return f"{case_id}:{sha256_text(canonical_json(case))}"


def _validate_case_population_derivation(
    derivation: Any,
    *,
    source_record: dict[str, Any] | None,
    repaired_record: dict[str, Any],
    source_by_name: dict[str, list[ArtifactRow]],
    population_match_cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]] | None]
    | None = None,
) -> str | None:
    if not isinstance(derivation, dict):
        return "derivation_not_object"
    rule_id = derivation.get("rule_id")
    if rule_id == "derived_brain_record_from_explicit_case_artifact.v1":
        return _validate_derived_brain_record_from_case(
            derivation,
            repaired_record=repaired_record,
            source_by_name=source_by_name,
        )
    if rule_id == "case_id_from_unique_evidence_join.v2":
        return _validate_case_population_derivation_v2(
            derivation,
            source_record=source_record,
            repaired_record=repaired_record,
            source_by_name=source_by_name,
            population_match_cache=population_match_cache,
        )
    if rule_id != "case_id_from_unique_outcome_leader.v1":
        return "unknown_rule"
    block_name = _string(derivation.get("source_artifact"))
    source_case_id = _string(derivation.get("source_case_id"))
    target_field = _string(derivation.get("target_field"))
    leader_id = _string(derivation.get("join_value"))
    spec = _DERIVED_CASE_TARGETS.get(block_name or "")
    if (
        block_name is None
        or spec is None
        or source_case_id is None
        or target_field != spec[1]
        or leader_id is None
        or source_record is None
    ):
        return "identity_contract_invalid"
    cases = [
        row.row
        for row in source_by_name.get(block_name or "", [])
        if source_case_id
        in _field_string_values(
            row.row,
            *_CASE_ID_ALIASES.get(block_name or "", (target_field,)),
        )
        and _string(row.row.get("outcome_leader_id")) == leader_id
    ]
    if len(cases) != 1:
        return "source_case_not_unique"
    case = cases[0]
    if derivation.get("source_case_payload_sha256") != sha256_text(canonical_json(case)):
        return "source_case_hash_mismatch"
    if repaired_record.get("record_type") != spec[0]:
        return "record_type_mismatch"
    if repaired_record.get(target_field) != source_case_id:
        return "target_field_mismatch"
    payload = source_record.get("payload")
    source_leader_id = _string(source_record.get("outcome_leader_id")) or (
        _string(payload.get("outcome_leader_id")) if isinstance(payload, dict) else None
    )
    if source_leader_id != leader_id:
        return "source_brain_join_mismatch"
    for field in ("ticker", "trade_date"):
        case_value = _string(case.get(field))
        source_value = _string(source_record.get(field))
        if case_value and source_value and case_value != source_value:
            return f"{field}_mismatch"
    return None


def _validate_derived_brain_record_from_case(
    derivation: dict[str, Any],
    *,
    repaired_record: dict[str, Any],
    source_by_name: dict[str, list[ArtifactRow]],
) -> str | None:
    """Validate a brain row copied from an explicit first-class case artifact."""

    block_name = _string(derivation.get("source_artifact"))
    source_case_id = _string(derivation.get("source_case_id"))
    target_field = _string(derivation.get("target_field"))
    spec = _DERIVED_CASE_TARGETS.get(block_name or "")
    expected_record_types = {spec[0]} if spec is not None else set()
    if block_name == "theme_formation_cases.jsonl":
        expected_record_types.add("theme_formation_case")
    if (
        block_name is None
        or spec is None
        or source_case_id is None
        or target_field != spec[1]
        or repaired_record.get("record_type") not in expected_record_types
        or repaired_record.get(target_field) != source_case_id
    ):
        return "identity_contract_invalid"
    cases = [
        row.row
        for row in source_by_name.get(block_name, [])
        if source_case_id
        in _field_string_values(
            row.row,
            *_CASE_ID_ALIASES.get(block_name, (target_field,)),
        )
    ]
    if len(cases) != 1:
        return "source_case_not_unique"
    case = cases[0]
    if derivation.get("source_case_payload_sha256") != sha256_text(canonical_json(case)):
        return "source_case_hash_mismatch"
    pair_eligibility_downgrade = bool(
        block_name == "blind_leader_preference_pairs.jsonl"
        and case.get("training_eligible") is True
        and not has_sealed_preference_pair(case)
        and repaired_record.get("training_eligible") is False
        and _float(repaired_record.get("sample_weight")) == 0.0
        and _string(repaired_record.get("training_exclusion_reason"))
        == "sealed_preference_pair_missing"
    )
    reference_token_fields = {
        "source_fact_ids": "legacy_unresolved_fact_tokens",
        "fact_ids": "legacy_unresolved_fact_tokens",
        "source_inference_ids": "legacy_unresolved_inference_tokens",
        "inference_ids": "legacy_unresolved_inference_tokens",
    }
    for field, value in case.items():
        if repaired_record.get(field) == value:
            continue
        if pair_eligibility_downgrade and field in {
            "training_eligible",
            "sample_weight",
        }:
            continue
        legacy_field = reference_token_fields.get(field)
        source_values = set(_string_list(value))
        repaired_values = set(_string_list(repaired_record.get(field)))
        legacy_values = (
            set(_string_list(repaired_record.get(legacy_field)))
            if legacy_field is not None
            else set()
        )
        if (
            legacy_field is not None
            and source_values
            and repaired_values <= source_values
            and repaired_values | (legacy_values & source_values) == source_values
            and _string(repaired_record.get("unresolved_reference_reason"))
            == "typed_reference_not_present_in_bundle_ledger"
        ):
            continue
        return "derived_record_changed_source_case_field"
    case_eligible = case.get("training_eligible")
    if not isinstance(case_eligible, bool):
        return "source_case_eligibility_ambiguous"
    if (
        repaired_record.get("training_eligible") is not case_eligible
        and not pair_eligibility_downgrade
    ):
        return "derived_record_eligibility_changed"
    declared_weight = case.get("sample_weight")
    expected_weight = (
        _float(declared_weight)
        if declared_weight is not None
        else 1.0
        if case_eligible
        else 0.0
    )
    if pair_eligibility_downgrade:
        expected_weight = 0.0
    if _float(repaired_record.get("sample_weight")) != expected_weight:
        return "derived_record_weight_changed"
    if not case_eligible and _string(
        case.get("training_exclusion_reason")
        or case.get("eligibility_reason")
        or case.get("no_direct_bridge_reason")
        or case.get("exclusion_reason")
        or case.get("case_status")
        or case.get("classification")
    ) is None:
        return "source_case_missing_exclusion_reason"
    case_facts = set(
        _string_list(case.get("source_fact_ids"))
        + _string_list(case.get("fact_ids"))
    )
    record_facts = set(
        _string_list(repaired_record.get("source_fact_ids"))
        + _string_list(repaired_record.get("fact_ids"))
    )
    legacy_facts = set(
        _string_list(repaired_record.get("legacy_unresolved_fact_tokens"))
    )
    if record_facts | (legacy_facts & case_facts) != case_facts:
        return "derived_record_fact_changed"
    case_inferences = set(
        _string_list(case.get("source_inference_ids"))
        + _string_list(case.get("inference_ids"))
    )
    record_inferences = set(
        _string_list(repaired_record.get("source_inference_ids"))
        + _string_list(repaired_record.get("inference_ids"))
    )
    legacy_inferences = set(
        _string_list(repaired_record.get("legacy_unresolved_inference_tokens"))
    )
    if record_inferences | (legacy_inferences & case_inferences) != case_inferences:
        return "derived_record_inference_changed"
    case_sources = set(
        _string_list(case.get("source_ids"))
        + _string_list(case.get("provenance_source_ids"))
        + _string_list(case.get("matched_source_row_ids"))
        + _string_list(case.get("matched_source_ids"))
    )
    record_sources = set(
        _string_list(repaired_record.get("provenance_source_ids"))
        + _string_list(repaired_record.get("source_ids"))
    )
    if record_sources != case_sources:
        return "derived_record_source_changed"
    expected_inputs = {
        source_case_id,
        *case_facts,
        *case_inferences,
        *case_sources,
    }
    if set(_string_list(derivation.get("derivation_inputs"))) != expected_inputs:
        return "derivation_inputs_mismatch"
    return None


def _validate_case_population_derivation_v2(
    derivation: dict[str, Any],
    *,
    source_record: dict[str, Any] | None,
    repaired_record: dict[str, Any],
    source_by_name: dict[str, list[ArtifactRow]],
    population_match_cache: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]] | None]
    | None = None,
) -> str | None:
    block_name = _string(derivation.get("source_artifact"))
    source_case_id = _string(derivation.get("source_case_id"))
    target_field = _string(derivation.get("target_field"))
    spec = _DERIVED_CASE_TARGETS.get(block_name or "")
    if spec is None or source_case_id is None or target_field != spec[1] or source_record is None:
        return "identity_contract_invalid"
    cases = [
        row.row
        for row in source_by_name.get(block_name or "", [])
        if source_case_id
        in _field_string_values(
            row.row,
            *_CASE_ID_ALIASES.get(block_name or "", (target_field,)),
        )
    ]
    if len(cases) != 1 and block_name == "negative_control_cases.jsonl":
        declared_hash = _string(derivation.get("source_case_payload_sha256"))
        hashed_cases = [
            candidate
            for candidate in cases
            if declared_hash is not None
            and sha256_text(canonical_json(candidate)) == declared_hash
        ]
        if len(hashed_cases) == 1:
            cases = hashed_cases
    if len(cases) != 1:
        return "source_case_not_unique"
    case = cases[0]
    if derivation.get("source_case_payload_sha256") != sha256_text(canonical_json(case)):
        return "source_case_hash_mismatch"

    # Some legacy bundles represent one issuer-day aggregate as several
    # direct-event records, one per sealed fact.  The repair side records the
    # aggregate case id on each contributing direct row; validate the whole
    # fact partition rather than requiring a single record to contain every
    # fact from the aggregate.
    if block_name == "issuer_day_cases.jsonl" and repaired_record.get(
        "record_type"
    ) == "supervised_direct_event_case":
        return _validate_split_issuer_day_derivation(
            case,
            source_case_id=source_case_id,
            target_field=target_field,
            derivation=derivation,
            source_record=source_record,
            repaired_record=repaired_record,
            source_by_name=source_by_name,
        )

    source_brain_rows = [row.row for row in source_by_name.get("brain_delta.jsonl", [])]
    cache = population_match_cache
    if cache is None:
        best = _best_case_population_match(
            block_name or "",
            case,
            canonical_record_type=spec[0],
            records=source_brain_rows,
        )
    else:
        best = cache.get(
            (
                block_name or "",
                _case_population_cache_id(source_case_id, case),
            )
        )
    if best is None:
        return "source_brain_join_not_unique"
    matched_record, evidence = best
    if _record_id(matched_record) != _record_id(source_record):
        return "source_brain_join_mismatch"

    competing_case_rows: list[tuple[dict[str, Any], set[str]]] = []
    aliases = _CASE_ID_ALIASES.get(block_name or "", (target_field,))
    for artifact_row in source_by_name.get(block_name or "", []):
        competing_case = artifact_row.row
        competing_aliases = _field_string_values(competing_case, *aliases)
        if not competing_aliases:
            continue
        # Match once per source row, not once per alias.  A single legacy
        # case row commonly carries both ``blind_pair_id`` and ``case_id``;
        # counting those aliases as two competing cases creates a false
        # non-one-to-one result.
        if cache is None:
            competing_match = _best_case_population_match(
                block_name or "",
                competing_case,
                canonical_record_type=spec[0],
                records=source_brain_rows,
            )
        else:
            competing_match = next(
                (
                    cache.get(
                        (
                            block_name or "",
                            _case_population_cache_id(alias, competing_case),
                        )
                    )
                    for alias in competing_aliases
                    if cache.get(
                        (
                            block_name or "",
                            _case_population_cache_id(alias, competing_case),
                        )
                    )
                    is not None
                ),
                None,
            )
        if (
            competing_match is not None
            and _record_id(competing_match[0]) == _record_id(source_record)
        ):
            competing_case_rows.append((competing_case, competing_aliases))
    if len(competing_case_rows) != 1 or source_case_id not in competing_case_rows[0][1]:
        return "source_case_to_brain_not_one_to_one"

    expected_relation = f"{block_name}:{case.get('classification')}->{source_record.get('record_type')}"
    declared_join_field = _string(derivation.get("join_field"))
    declared_join_value = _string(derivation.get("join_value"))
    declared_join_is_observed = bool(
        declared_join_field
        and declared_join_value
        and declared_join_value in evidence["join_values"].get(declared_join_field, [])
    )
    if (
        not declared_join_is_observed
        or derivation.get("record_type_relation") != expected_relation
    ):
        return "derivation_evidence_mismatch"
    if not _declared_join_values_match(derivation.get("join_values"), evidence["join_values"]):
        return "derivation_evidence_mismatch"
    if repaired_record.get(target_field) != source_case_id:
        return "target_field_mismatch"
    return None


def _validate_split_issuer_day_derivation(
    case: dict[str, Any],
    *,
    source_case_id: str,
    target_field: str,
    derivation: dict[str, Any],
    source_record: dict[str, Any],
    repaired_record: dict[str, Any],
    source_by_name: dict[str, list[ArtifactRow]],
) -> str | None:
    if repaired_record.get(target_field) != source_case_id:
        return "target_field_mismatch"

    case_fact_ids = _case_relation_values(
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
    record_fact_ids = _case_relation_values(
        source_record,
        "source_fact_ids",
        "fact_ids",
        "blind_fact_ids",
        "sealed_fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    )
    shared_facts = case_fact_ids & record_fact_ids
    if not case_fact_ids or not shared_facts or not record_fact_ids.issubset(case_fact_ids):
        return "split_case_fact_partition_invalid"

    case_ticker = _first(case, "ticker", "candidate_ticker")
    record_ticker = _first(source_record, "ticker", "candidate_ticker")
    if case_ticker and record_ticker and case_ticker != record_ticker:
        return "ticker_mismatch"
    case_trade_date = _string(case.get("trade_date"))
    record_trade_date = _string(source_record.get("trade_date"))
    if case_trade_date and record_trade_date and case_trade_date != record_trade_date:
        return "trade_date_mismatch"

    brain_rows = [row.row for row in source_by_name.get("brain_delta.jsonl", [])]
    contributing: list[dict[str, Any]] = []
    for row in brain_rows:
        # An issuer-day aggregate can span several existing brain lanes.  A
        # direct-event row proves the requested source case itself, while an
        # issuer-day/candidate-audit row may carry the aggregate's other
        # sealed facts.  Count only rows whose explicit facts are a subset of
        # this case and whose ticker/date match; no new evidence is inferred.
        if row.get("record_type") not in {
            "supervised_direct_event_case",
            "supervised_issuer_day_case",
            "candidate_ranking_error_case",
            "candidate_generation_error_case",
            "beneficiary_discovery_case",
            "negative_control_case",
            "blind_leader_preference_pair",
            "newsless_or_unexplained_case",
            "counterexample",
            "entity_resolution_error_case",
            "context_market_state_or_fact_case",
        }:
            continue
        row_ticker = _first(row, "ticker", "candidate_ticker")
        row_date = _string(row.get("trade_date"))
        if case_ticker and row_ticker and row_ticker != case_ticker:
            continue
        if case_trade_date and row_date and row_date != case_trade_date:
            continue
        row_fact_ids = _case_relation_values(
            row,
            "source_fact_ids",
            "fact_ids",
            "blind_fact_ids",
            "sealed_fact_ids",
            "blind_selected_fact_ids",
            "selected_fact_ids",
        )
        if not row_fact_ids or not row_fact_ids.issubset(case_fact_ids):
            continue
        shared = row_fact_ids & case_fact_ids
        if shared:
            contributing.append(row)
    if not contributing:
        return "split_case_no_contributors"

    covered: set[str] = set()
    for row in contributing:
        covered.update(
            _case_relation_values(
                row,
                "source_fact_ids",
                "fact_ids",
                "blind_fact_ids",
                "sealed_fact_ids",
                "blind_selected_fact_ids",
                "selected_fact_ids",
            )
        )
    if covered != case_fact_ids:
        return "split_case_fact_coverage_incomplete"

    source_id = _record_id(source_record)
    if source_id is None or not any(_record_id(row) == source_id for row in contributing):
        return "split_case_source_row_not_contributor"

    # A direct row must not be claimed by two different issuer aggregates.
    competing_case_ids: list[str] = []
    for artifact_row in source_by_name.get("issuer_day_cases.jsonl", []):
        competing_case = artifact_row.row
        competing_id = _first(
            competing_case,
            *_CASE_ID_ALIASES.get("issuer_day_cases.jsonl", (target_field,)),
        )
        if competing_id is None:
            continue
        competing_facts = _case_relation_values(
            competing_case,
            "matched_fact_ids",
            "sealed_fact_ids",
            "sealed_source_fact_ids",
            "source_fact_ids",
            "fact_ids",
            "blind_selected_fact_ids",
            "selected_fact_ids",
        )
        if competing_id == source_case_id or not (record_fact_ids & competing_facts):
            continue
        competing_ticker = _first(competing_case, "ticker", "candidate_ticker")
        competing_date = _string(competing_case.get("trade_date"))
        if competing_ticker and record_ticker and competing_ticker != record_ticker:
            continue
        if competing_date and record_trade_date and competing_date != record_trade_date:
            continue
        competing_case_ids.append(competing_id)
    if competing_case_ids:
        return "split_case_fact_claim_ambiguous"

    evidence = _case_population_join_evidence_for_validation(
        case,
        source_record,
        allow_partial_case_facts=True,
    )
    if evidence is None:
        return "split_case_join_evidence_missing"
    expected_relation = f"issuer_day_cases.jsonl:{case.get('classification')}->{source_record.get('record_type')}"
    expected_values = {
        "join_field": evidence["primary_join_field"],
        "join_value": evidence["primary_join_value"],
        "record_type_relation": expected_relation,
    }
    if any(derivation.get(field) != expected for field, expected in expected_values.items()):
        return "derivation_evidence_mismatch"
    if not _declared_join_values_match(derivation.get("join_values"), evidence["join_values"]):
        return "derivation_evidence_mismatch"
    return None


def _declared_join_values_match(declared: Any, observed: Any) -> bool:
    """Accept a declared join when the audit observes additional aliases."""

    if not isinstance(declared, dict) or not isinstance(observed, dict) or not declared:
        return False
    for field, values in declared.items():
        if field not in observed:
            return False
        declared_values = set(_string_list(values))
        observed_values = set(_string_list(observed[field]))
        if not declared_values.issubset(observed_values):
            return False
    return True


def _best_case_population_match(
    block_name: str,
    case: dict[str, Any],
    *,
    canonical_record_type: str,
    records: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    allowed_record_types = _case_population_record_types_for_validation(
        block_name,
        case,
        canonical_record_type=canonical_record_type,
    )
    for record in records:
        if record.get("record_type") not in allowed_record_types:
            continue
        evidence = _case_population_join_evidence_for_validation(
            case,
            record,
            allow_partial_case_facts=block_name == "ranking_error_cases.jsonl",
        )
        if evidence is None:
            continue
        candidates.append((record, evidence))
    if not candidates:
        return None

    preferred_record_types = _preferred_case_record_types(
        block_name,
        available_record_types={
            record_type
            for record, _ in candidates
            for record_type in [record.get("record_type")]
            if isinstance(record_type, str)
        },
        canonical_record_type=canonical_record_type,
    )
    matches: list[tuple[int, dict[str, Any], dict[str, Any]]] = []
    for record, evidence in candidates:
        if block_name == "ranking_error_cases.jsonl":
            # The repair adapter uses the specific legacy alias for ranking
            # misses. Prefer that row over an issuer/direct mirror when the
            # evidence fields are otherwise identical.
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
        if block_name == "blind_leader_preference_pairs.jsonl":
            case_sequence = _case_relation_sequence(
                case,
                "source_fact_ids",
                "fact_ids",
            )
            record_sequence = _case_relation_sequence(
                record,
                "source_fact_ids",
                "fact_ids",
            )
            score += int(
                bool(case_sequence)
                and bool(record_sequence)
                and case_sequence == record_sequence
            ) * 40
        score += int(evidence["ticker_matches"]) * 2
        matches.append((score, record, evidence))
    best_score = max(score for score, _, _ in matches)
    best_matches = [item for item in matches if item[0] == best_score]
    if len(best_matches) != 1:
        return None
    _, record, evidence = best_matches[0]
    return record, evidence


def _preferred_case_record_types(
    block_name: str,
    *,
    available_record_types: set[str],
    canonical_record_type: str,
) -> set[str]:
    """Choose a representation without treating context as a tie.

    Theme artifacts exist in both the old ``theme_formation_case`` spelling
    and the supervised importer spelling.  A zero-weight context mirror may
    also join the same facts.  Prefer the most specific brain representation
    actually present in this bundle; never let a hard-coded date/type guess
    decide the join.
    """

    if block_name == "theme_formation_cases.jsonl":
        for record_type in (
            "theme_formation_case",
            "supervised_theme_formation_case",
            canonical_record_type,
            "context_market_state_or_fact_case",
        ):
            if record_type in available_record_types:
                return {record_type}
    return {canonical_record_type}


def _case_population_record_types_for_validation(
    block_name: str,
    case: dict[str, Any],
    *,
    canonical_record_type: str,
) -> set[str]:
    if block_name == "direct_event_cases.jsonl":
        allowed = {canonical_record_type}
        # Older bundles represent excluded/watch direct observations in the
        # existing negative, ranking, beneficiary, or pair lanes.  The
        # validator still requires an exact fact/ticker/date join below; this
        # only recognizes the representation without fabricating a record.
        allowed.update(
            {
                "negative_control_case",
                "candidate_ranking_error_case",
                "candidate_generation_error_case",
                "beneficiary_discovery_case",
                "blind_leader_preference_pair",
                "counterexample",
            }
        )
        # A rejected direct-event observation can be intentionally represented
        # by the same sealed candidate/fact in the negative-control and
        # issuer-day lanes.  Accept that representation only when the source
        # case itself explicitly says it is excluded, semantically failed,
        # and non-training.  This is a join alias, not a new direct-event
        # record and never promotes the alternate record.
        decision = str(case.get("screening_decision") or "").upper()
        verdict = str(case.get("semantic_verdict") or "").upper()
        if (
            case.get("training_eligible") is False
            and decision in {"EXCLUDE", "REJECT", "REJECTED", "AUDIT_ONLY"}
            and verdict in {"FAIL", "FAILED", "REJECT", "REJECTED"}
        ):
            allowed.update({"negative_control_case", "supervised_issuer_day_case"})
        if case.get("direct_event_eligible") is False and case.get("training_eligible") is False:
            # Explicitly rejected/non-eligible direct observations are often
            # represented in the brain as issuer-day or negative-control
            # memory, not as a second trainable direct-event row.
            allowed.update({"negative_control_case", "supervised_issuer_day_case"})
        return allowed
    if block_name == "issuer_day_cases.jsonl":
        # Legacy bundles may split one issuer-day aggregate across direct
        # event rows, one row per sealed fact.  The repair lineage validates
        # the complete fact partition before accepting this representation.
        return {
            canonical_record_type,
            "supervised_direct_event_case",
            "negative_control_case",
            "candidate_ranking_error_case",
            "candidate_generation_error_case",
            "beneficiary_discovery_case",
            "blind_leader_preference_pair",
            "counterexample",
        }
    if block_name == "negative_control_cases.jsonl":
        risk_flags = {value.upper() for value in _string_list(case.get("semantic_risk_flags"))}
        decision = str(case.get("screening_decision") or "").upper()
        allowed = {canonical_record_type, "row_disposition_error_case"}
        # Some legacy bundles keep the exact negative-control fact on an
        # existing direct-event/issuer-day row. This attaches only the source
        # case ID; it does not create a new judgment or promote the row.
        allowed.update({"supervised_direct_event_case", "supervised_issuer_day_case"})
        if (
            str(case.get("selection_reason") or "").upper()
            in {
                "EXPLICIT_NEGATIVE_CONTROL_SOURCE",
                "RANKABLE_NOT_FINAL_REPRESENTATIVE",
            }
        ):
            # Some legacy bundles keep this explicit, non-training source
            # control as a ``counterexample`` record while preserving the
            # exact negative-control ID in its payload.
            allowed.add("counterexample")
        if (
            _first(case, "ticker", "code", "company_name", "company") is None
            and "NO_LOCAL_ISSUER_OWNER" in risk_flags
            and decision in {"AUDIT_ONLY", "REJECT_SEMANTIC_FALSE_POSITIVE"}
        ):
            allowed.add("counterexample")
        if str(case.get("selection_basis") or "").upper() == "REPRESENTATIVE_EXCLUDE_AUDIT_COMBINATION":
            # This legacy representative lane is preserved as a
            # source-backed counterexample with the exact negative_control_id
            # in its payload.
            allowed.add("counterexample")
        return allowed
    if block_name == "ranking_error_cases.jsonl":
        # A ranking-miss case can be represented by an existing exact
        # issuer-day aggregate or direct-event fact row in legacy bundles.
        allowed = {
            canonical_record_type,
            "candidate_ranking_error_case",
            "supervised_issuer_day_case",
            "supervised_direct_event_case",
        }
        if str(case.get("case_type") or "").upper() == "NEGATIVE_CONTROL":
            allowed.add("negative_control_case")
        return allowed
    if block_name == "theme_formation_cases.jsonl":
        allowed = {
            canonical_record_type,
            "supervised_theme_formation_case",
            "theme_formation_case",
        }
        # Legacy bundles sometimes keep an explicitly non-eligible theme or
        # market-state case as a zero-weight context record.  The exact
        # source-fact/source-row join is the identity evidence; this does not
        # promote the row or invent a theme judgment.
        # The same representation also occurs for eligible continuation and
        # market-state themes.  It is accepted only by the exact case/fact
        # join in the population matcher below; no new theme record is made.
        allowed.add("context_market_state_or_fact_case")
        return allowed
    if block_name == "newsless_or_unexplained_cases.jsonl":
        classification = str(case.get("classification") or "").upper()
        allowed = {canonical_record_type}
        # A newsless leader is sometimes materialized in brain_delta as the
        # source-backed candidate-generation error row instead of a second
        # ``newsless_or_unexplained_case`` row.  Accept that representation
        # only when the source explicitly records that no cutoff-safe fact was
        # found.  The join matcher still requires the leader/ticker/date (or
        # another explicit relation), so this never invents a row.
        sealed_match = str(case.get("sealed_source_match") or "").upper()
        explanation_status = str(case.get("explanation_status") or "").upper()
        error_type = str(case.get("error_type") or "").upper()
        matched_facts = _case_relation_values(
            case,
            "matched_fact_ids",
            "sealed_fact_ids",
            "sealed_source_fact_ids",
            "source_fact_ids",
            "fact_ids",
        )
        no_cutoff_safe_fact = (
            not matched_facts
            and (
                sealed_match in {"NONE", "NO_MATCH", "NO_CUTOFF_SAFE_SOURCE"}
                or explanation_status.startswith("UNEXPLAINED")
                or "NO_CUTOFF_SAFE" in error_type
            )
        )
        if case.get("training_eligible") is False and no_cutoff_safe_fact:
            allowed.add("candidate_generation_error_case")
        if classification == "SEMANTIC_FALSE_POSITIVE":
            # Some legacy bundles keep this rejected, non-eligible lane as an
            # entity-resolution error record rather than a newsless record.
            # The source fact/row join remains mandatory below.
            allowed.add("entity_resolution_error_case")
        return allowed
    if block_name == "context_market_state_or_fact_cases.jsonl":
        # Market-state context is intentionally stored as mechanism memory in
        # some quarantined/legacy bundles.  The payload still carries the
        # explicit context_case_id; this alias does not make it trainable.
        return {canonical_record_type, "mechanism_memory"}
    if block_name != "beneficiary_discovery_cases.jsonl":
        return {canonical_record_type}
    classification = str(
        case.get("classification")
        or case.get("discovery_type")
        or case.get("discovery_classification")
        or case.get("discovery_class")
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
        "RETROSPECTIVE_CANDIDATE_GENERATION_GAP": {
            "candidate_generation_error_case"
        },
        "SEALED_SOURCE_PRESENT_BUT_NOT_FINAL": {
            "supervised_issuer_day_case",
            "supervised_direct_event_case",
            "ranking_error_case",
        },
    }
    return {canonical_record_type, *aliases.get(classification, set())}


def _case_population_join_evidence_for_validation(
    case: dict[str, Any],
    record: dict[str, Any],
    *,
    allow_partial_case_facts: bool = False,
) -> dict[str, Any] | None:
    case_leaders = _case_relation_values(case, "outcome_leader_id")
    record_leaders = _case_relation_values(record, "outcome_leader_id")
    case_candidates = _case_relation_values(case, "candidate_id", "candidate_ids")
    record_candidates = _case_relation_values(record, "candidate_id", "candidate_ids")
    case_themes = _case_relation_values(
        case,
        "theme_id",
        "theme_ids",
        "theme_case_id",
        "theme_case_ids",
        "theme",
        "theme_key",
        "theme_keys",
    )
    record_themes = _case_relation_values(
        record,
        "theme_id",
        "theme_ids",
        "theme_case_id",
        "theme_case_ids",
        "theme",
        "theme_key",
        "theme_keys",
    )
    case_screenings = _case_relation_values(
        case,
        "source_screening_id",
        "source_screening_ids",
        "screening_id",
        "screening_ids",
    )
    record_screenings = _case_relation_values(
        record,
        "source_screening_id",
        "source_screening_ids",
        "screening_id",
        "screening_ids",
    )
    case_audits = _case_relation_values(
        case,
        "audit_id",
        "audit_ids",
        "outcome_audit_id",
        "outcome_audit_ids",
    )
    record_audits = _case_relation_values(
        record,
        "audit_id",
        "audit_ids",
        "outcome_audit_id",
        "outcome_audit_ids",
    )
    case_fact_ids = _case_relation_values(
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
    record_fact_ids = _case_relation_values(
        record,
        "source_fact_ids",
        "fact_ids",
        "blind_fact_ids",
        "sealed_fact_ids",
        "blind_selected_fact_ids",
        "selected_fact_ids",
    )
    shared_leaders = _shared_relation_values(case_leaders, record_leaders)
    shared_candidates = _shared_relation_values(case_candidates, record_candidates)
    shared_themes = _shared_relation_values(case_themes, record_themes)
    shared_screenings = _shared_relation_values(case_screenings, record_screenings)
    shared_audits = _shared_relation_values(case_audits, record_audits)
    shared_facts = _shared_relation_values(case_fact_ids, record_fact_ids)
    case_ticker = _first(case, "ticker", "candidate_ticker")
    record_ticker = _first(record, "ticker", "candidate_ticker")
    ticker_matches = bool(case_ticker and record_ticker and case_ticker == record_ticker)
    case_trade_date = _string(case.get("trade_date"))
    record_trade_date = _string(record.get("trade_date"))
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
        and not (
            allow_partial_case_facts
            and bool(case_fact_ids & record_fact_ids)
        )
    ):
        return None
    case_classification = _string(case.get("classification"))
    record_classifications = _case_relation_values(
        record,
        "classification",
        "postmortem_class",
    )
    classification_matches = bool(case_classification and case_classification in record_classifications)
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
        "ticker_matches": bool(case_ticker and record_ticker),
        "join_values": join_values,
        "primary_join_field": primary_join_field,
        "primary_join_value": join_values[primary_join_field][0],
    }


def _case_relation_values(row: dict[str, Any], *fields: str) -> set[str]:
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


def _case_relation_sequence(row: dict[str, Any], *fields: str) -> list[str]:
    """Keep the first source-declared relation order for directed joins."""

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


def _shared_relation_values(left: set[str], right: set[str]) -> list[str]:
    """Join legacy relation IDs while preserving the left/source spelling."""

    right_keys = {_relation_alias_key(value) for value in right}
    return sorted(value for value in left if _relation_alias_key(value) in right_keys)


def _relation_alias_key(value: str) -> str:
    return re.sub(r"\d+", lambda match: str(int(match.group(0))), value)


def _unique_artifact_row_index(
    rows: list[ArtifactRow],
    key_fields: tuple[str, ...],
) -> dict[str, ArtifactRow]:
    index: dict[str, ArtifactRow] = {}
    for row in rows:
        key = _first(row.row, *key_fields)
        if key is not None and "record_id" in key_fields:
            key = key.rsplit("__", 1)[-1]
        if key is not None and key not in index:
            index[key] = row
    return index


def _is_repair_placeholder_source(row: dict[str, Any]) -> bool:
    return (
        row.get("provenance_placeholder") is True
        or "PLACEHOLDER" in str(row.get("source_type") or row.get("source_kind") or "").upper()
    )


def _candidate_semantic_alias_transform_valid(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    repaired_by_name: dict[str, list[ArtifactRow]],
) -> bool:
    changed_fields = {field for field in set(before) | set(after) if before.get(field) != after.get(field)}
    if changed_fields != {
        "local_predicate_owner_is_candidate",
        "target_issuer_is_article_subject",
        "semantic_alias_repair_provenance",
    }:
        return False
    if (
        before.get("local_predicate_owner_is_candidate") is not False
        or before.get("target_issuer_is_article_subject") is not False
        or after.get("local_predicate_owner_is_candidate") is not True
        or after.get("target_issuer_is_article_subject") is not True
    ):
        return False
    provenance = after.get("semantic_alias_repair_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("rule_id") != "semantic_owner_from_verified_historical_alias.v1"
    ):
        return False
    entity_resolution_id = _string(provenance.get("entity_resolution_id"))
    final_witness_id = _string(provenance.get("final_evidence_witness_id"))
    candidate_id = _string(before.get("candidate_id"))
    candidate_company = _string(before.get("candidate_company") or before.get("company"))
    ticker = _string(before.get("ticker"))
    source_id = _first(before, "source_id", "source_row_id", "row_id")
    if (
        entity_resolution_id is None
        or final_witness_id is None
        or candidate_id is None
        or candidate_company is None
        or ticker is None
        or source_id is None
        or provenance.get("candidate_id") != candidate_id
        or provenance.get("source_id") != source_id
        or provenance.get("ticker") != ticker
    ):
        return False
    entities = [
        row.row
        for row in repaired_by_name.get("entity_resolution.jsonl", [])
        if _first(row.row, "entity_resolution_id", "resolution_id") == entity_resolution_id
    ]
    final_witnesses = [
        row.row
        for row in repaired_by_name.get("final_evidence_witness.jsonl", [])
        if _first(row.row, "final_evidence_witness_id", "witness_id") == final_witness_id
    ]
    if len(entities) != 1 or len(final_witnesses) != 1:
        return False
    entity = entities[0]
    final_witness = final_witnesses[0]
    return all(
        (
            _first(entity, "source_id", "source_row_id", "row_id") == source_id,
            _string(entity.get("canonical_company")) == candidate_company,
            _string(entity.get("ticker")) == ticker,
            entity.get("local_ticker_ownership_verified") is True,
            str(entity.get("resolution_status") or "").startswith("RESOLVED"),
            _string(entity.get("local_predicate_owner")) == _string(before.get("local_predicate_owner")),
            _string(final_witness.get("candidate_id")) == candidate_id,
            _string(final_witness.get("candidate_company")) == candidate_company,
            _string(final_witness.get("ticker")) == ticker,
            _string(final_witness.get("primary_fact_id")) == _string(before.get("primary_fact_id")),
            _string(final_witness.get("primary_quote")) == _string(before.get("primary_quote")),
            final_witness.get("local_predicate_owner_is_candidate") is True,
            final_witness.get("target_issuer_is_article_subject") is True,
            final_witness.get("issuer_role_anchor_valid") is True,
            _semantic_verdict_value(final_witness) in {"PASS", "PASSED"},
        )
    )


def _semantic_primary_fact_transform_valid(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    repaired_by_name: dict[str, list[ArtifactRow]],
) -> bool:
    changed_fields = {
        field
        for field in set(before) | set(after)
        if before.get(field) != after.get(field)
    }
    required_changes = {
        "primary_fact_id",
        "primary_quote",
        "semantic_fact_reference_repair_provenance",
    }
    allowed_changes = {*required_changes, "source_id", "source_row_id"}
    if not required_changes.issubset(changed_fields) or not changed_fields.issubset(
        allowed_changes
    ):
        return False
    provenance = after.get("semantic_fact_reference_repair_provenance")
    if (
        not isinstance(provenance, dict)
        or provenance.get("rule_id")
        != "primary_fact_from_unique_declared_candidate_surface.v1"
    ):
        return False
    candidate_id = _string(before.get("candidate_id"))
    candidate_company = _string(
        before.get("candidate_company")
        or before.get("company")
    )
    prior_fact_id = _first(
        before,
        "primary_fact_id",
        "source_fact_id",
        "fact_id",
    )
    replacement_fact_id = _string(provenance.get("replacement_primary_fact_id"))
    screening_id = _string(provenance.get("screening_id"))
    if (
        candidate_id is None
        or candidate_company is None
        or prior_fact_id is None
        or replacement_fact_id is None
        or screening_id is None
        or provenance.get("candidate_id") != candidate_id
        or provenance.get("candidate_company") != candidate_company
        or provenance.get("prior_primary_fact_id") != prior_fact_id
        or replacement_fact_id == prior_fact_id
    ):
        return False

    screenings = [
        row.row
        for row in repaired_by_name.get("candidate_screening.jsonl", [])
        if _first(row.row, "screening_id") == screening_id
        and _first(row.row, "candidate_id") == candidate_id
    ]
    if len(screenings) != 1:
        return False
    declared_fact_ids = _string_list(screenings[0].get("source_fact_ids"))
    if prior_fact_id not in declared_fact_ids or replacement_fact_id not in declared_fact_ids:
        return False
    facts = {
        fact_id: row.row
        for row in repaired_by_name.get("fact_ledger_blind.jsonl", [])
        for fact_id in [_first(row.row, "fact_id")]
        if fact_id is not None and fact_id in declared_fact_ids
    }
    if set(facts) != set(declared_fact_ids):
        return False
    surface = _semantic_company_surface(candidate_company)
    if not surface:
        return False
    prior_quote = _first(facts[prior_fact_id], "exact_quote")
    if surface in _semantic_company_surface(prior_quote):
        return False
    matching_fact_ids = [
        fact_id
        for fact_id in declared_fact_ids
        if surface
        in _semantic_company_surface(
            facts[fact_id].get("exact_quote")
        )
    ]
    if matching_fact_ids != [replacement_fact_id]:
        return False
    replacement_fact = facts[replacement_fact_id]
    replacement_quote = _first(replacement_fact, "exact_quote")
    replacement_source_id = _first(
        replacement_fact,
        "source_row_id",
        "source_id",
    )
    if replacement_quote is None or replacement_source_id is None:
        return False
    if (
        after.get("primary_fact_id") != replacement_fact_id
        or after.get("primary_quote") != replacement_quote
        or provenance.get("replacement_fact_sha256")
        != sha256_text(canonical_json(replacement_fact))
    ):
        return False
    if "source_id" in before and "source_row_id" not in before:
        if after.get("source_id") != replacement_source_id:
            return False
    elif after.get("source_row_id") != replacement_source_id:
        return False
    return True


def _semantic_company_surface(value: Any) -> str:
    return "".join(
        character.lower()
        for character in str(value or "")
        if character.isalnum()
    )


def _resolved_source_semantic_failures(
    source_rows: list[ArtifactRow],
    repaired_rows: list[ArtifactRow],
    *,
    source_failures: set[str],
) -> set[str]:
    source_by_name = _rows_by_name(source_rows)
    repaired_by_name = _rows_by_name(repaired_rows)
    repaired_witnesses = {
        screening_id: row.row
        for row in repaired_by_name.get("candidate_semantic_witness.jsonl", [])
        for screening_id in [_first(row.row, "screening_id", "source_screening_id")]
        if screening_id is not None
    }
    resolved: set[str] = set()
    for source_row in source_by_name.get("candidate_semantic_witness.jsonl", []):
        screening_id = _first(
            source_row.row,
            "screening_id",
            "source_screening_id",
        )
        repaired = repaired_witnesses.get(screening_id or "")
        if repaired is None or not _candidate_semantic_alias_transform_valid(
            source_row.row,
            repaired,
            repaired_by_name=repaired_by_name,
        ):
            continue
        failure = f"candidate_semantic_witness.jsonl:{source_row.origin_key}:predicate_owner"
        if failure in source_failures:
            resolved.add(failure)
    return resolved


def _artifact_occurrence_lineage_audit(
    source_census: SourceCensus,
    repaired_census: SourceCensus,
) -> dict[str, Any]:
    rewritten = {
        "brain_delta.jsonl",
        "beneficiary_discovery_cases.jsonl",
        "blind_leader_pairs.jsonl",
        "blind_leader_preference_pairs.jsonl",
        "bundle_manifest.json",
        "canonical_graph.json",
        "candidate_generation_error_cases.jsonl",
        "candidate_ranking_error_cases.jsonl",
        "ranking_error_cases.jsonl",
        "direct_ingest_contract.json",
        "direct_event_cases.jsonl",
        "event_ledger.jsonl",
        "error_scope_audit.jsonl",
        "candidate_semantic_witness.jsonl",
        "final_evidence_witness.jsonl",
        "final_semantic_audit.jsonl",
        "material_review.jsonl",
        "context_market_state_cases.jsonl",
        "issuer_day_cases.jsonl",
        "negative_control_cases.jsonl",
        "newsless_or_unexplained_cases.jsonl",
        "outcome_to_news_audit.jsonl",
        "outcome_reverse_record_coverage.jsonl",
        "record_provenance_closure_audit.jsonl",
        "postmortem_summary.json",
        "postmortem_semantic_audit.jsonl",
        "postmortem_error_cases.jsonl",
        "repair_log.jsonl",
        "research_questions.jsonl",
        "research_episode.json",
        "retrospective_theme_member_edges.jsonl",
        "retrospective_theme_cases.jsonl",
        "source_ledger.jsonl",
        "theme_formation_cases.jsonl",
        "validation_report.json",
    }
    # Parse each bundle once.  The explanation checks below need row-level
    # evidence, but reparsing a multi-megabyte bundle once per changed artifact
    # makes a large month effectively quadratic in I/O and JSON decoding.
    source_artifact_rows = artifact_rows(source_census.source_path)
    repaired_artifact_rows = artifact_rows(repaired_census.source_path)
    source_payloads: dict[str, Counter[str]] = defaultdict(Counter)
    repaired_payloads: dict[str, Counter[str]] = defaultdict(Counter)
    for census, target in (
        (source_census, source_payloads),
        (repaired_census, repaired_payloads),
    ):
        for occurrence in census.artifact_occurrences:
            if occurrence.overlapping_alias or occurrence.canonical_name is None:
                continue
            target[occurrence.canonical_name][occurrence.canonical_payload_sha256 or occurrence.payload_sha256] += 1
    missing_names: list[str] = []
    changed_names: list[str] = []
    orphan_names: list[str] = []
    for name, source_hashes in source_payloads.items():
        repaired_hashes = repaired_payloads.get(name)
        if not repaired_hashes:
            missing_names.append(name)
        elif (
            name not in rewritten
            and source_hashes != repaired_hashes
            and not _artifact_occurrence_change_is_explained(
                name,
                source_path=source_census.source_path,
                repaired_path=repaired_census.source_path,
                source_artifact_rows=source_artifact_rows,
                repaired_artifact_rows=repaired_artifact_rows,
            )
        ):
            changed_names.append(name)
    for name in repaired_payloads:
        if name not in source_payloads and name not in rewritten:
            orphan_names.append(name)
    return {
        "artifact_occurrence_missing_count": len(missing_names),
        "artifact_occurrence_changed_count": len(changed_names),
        "artifact_occurrence_orphan_count": len(orphan_names),
        "artifact_occurrence_missing_names": sorted(missing_names),
        "artifact_occurrence_changed_names": sorted(changed_names),
        "artifact_occurrence_orphan_names": sorted(orphan_names),
    }


def _artifact_occurrence_change_is_explained(
    name: str,
    *,
    source_path: Path,
    repaired_path: Path,
    source_artifact_rows: list[ArtifactRow] | None = None,
    repaired_artifact_rows: list[ArtifactRow] | None = None,
) -> bool:
    if _artifact_occurrence_record_id_namespacing_only(
        name,
        source_artifact_rows=source_artifact_rows,
        repaired_artifact_rows=repaired_artifact_rows,
    ):
        return True
    if name in {"id_registry.json", "id_registry.jsonl"} and _id_registry_change_is_namespacing_only(
            name,
            source_path=source_path,
            repaired_path=repaired_path,
            source_artifact_rows=source_artifact_rows,
            repaired_artifact_rows=repaired_artifact_rows,
        ):
        return True
    if name in {"repair_log.json", "repair_log.jsonl"} and _repair_log_change_is_namespacing_only(
        name,
        source_path=source_path,
        repaired_path=repaired_path,
        source_artifact_rows=source_artifact_rows,
        repaired_artifact_rows=repaired_artifact_rows,
    ):
        return True
    if _artifact_occurrence_source_alias_only(
        name,
        source_path=source_path,
        repaired_path=repaired_path,
        source_artifact_rows=source_artifact_rows,
        repaired_artifact_rows=repaired_artifact_rows,
    ):
        return True
    namespaced_id_field = {
        "negative_control_selection.jsonl": "brain_record_id",
        "postmortem_supervised_population.jsonl": "linked_brain_record_ids",
        "context_case_source_selection.jsonl": "record_id",
        "selected_negative_control_sources.jsonl": "record_id",
        "rankable_candidate_brain_mapping.jsonl": "brain_record_id",
        "candidate_ranking_brain_linkage.jsonl": "brain_record_ids",
        "candidate_screening_brain_linkage.jsonl": "brain_record_ids",
        "final_watchlist_brain_linkage.jsonl": "brain_record_ids",
        "outcome_audit_brain_linkage.jsonl": "brain_record_ids",
    }.get(name)
    if namespaced_id_field is not None:
        source_rows = [
            row.row
            for row in (source_artifact_rows or artifact_rows(source_path))
            if row.canonical_name == name
        ]
        repaired_rows = [
            row.row
            for row in (repaired_artifact_rows or artifact_rows(repaired_path))
            if row.canonical_name == name
        ]
        if len(source_rows) != len(repaired_rows):
            return False
        for source_row, repaired_row in zip(source_rows, repaired_rows, strict=True):
            if set(source_row) != set(repaired_row):
                return False
            for key, value in source_row.items():
                if key == namespaced_id_field:
                    source_values = _string_list(value)
                    repaired_values = _string_list(repaired_row.get(key))
                    if len(source_values) != len(repaired_values) or any(
                        not _is_namespaced_value(source_value, repaired_value)
                        for source_value, repaired_value in zip(
                            source_values,
                            repaired_values,
                            strict=True,
                        )
                    ):
                        return False
                elif repaired_row.get(key) != value:
                    return False
        return True
    if name != "brain_delta_repair_receipt.json":
        return False
    source_rows = [
        row.row
        for row in (source_artifact_rows or artifact_rows(source_path))
        if row.canonical_name == name
    ]
    repaired_rows = [
        row.row
        for row in (repaired_artifact_rows or artifact_rows(repaired_path))
        if row.canonical_name == name
    ]
    if len(source_rows) != 1 or len(repaired_rows) != 1:
        return False
    source = source_rows[0]
    repaired = dict(repaired_rows[0])
    for field in ("added_record_ids", "reclassified_record_ids"):
        source_values = _string_list(source.get(field))
        repaired_values = _string_list(repaired.get(field))
        if len(source_values) != len(repaired_values) or any(
            not _is_namespaced_value(source_value, repaired_value)
            for source_value, repaired_value in zip(source_values, repaired_values, strict=True)
        ):
            return False
        repaired[field] = source_values
    return repaired == source


def _repair_log_change_is_namespacing_only(
    name: str,
    *,
    source_path: Path,
    repaired_path: Path,
    source_artifact_rows: list[ArtifactRow] | None = None,
    repaired_artifact_rows: list[ArtifactRow] | None = None,
) -> bool:
    """Allow only the repair log's deterministic brain-ID rewrite.

    The repair log is an audit artifact, not a place where new decisions may
    be introduced.  Its record-ID lists may acquire the episode namespace;
    every other field, list length, and value must remain byte-semantically
    equivalent after JSON normalization.
    """

    source_rows = [
        row.row
        for row in (source_artifact_rows or artifact_rows(source_path))
        if row.canonical_name == name
    ]
    repaired_rows = [
        row.row
        for row in (repaired_artifact_rows or artifact_rows(repaired_path))
        if row.canonical_name == name
    ]
    if len(source_rows) != 1 or len(repaired_rows) != 1:
        return False
    source = source_rows[0]
    repaired = repaired_rows[0]
    allowed_namespaced_fields = {
        "reclassified_record_ids",
        "new_brain_record_ids",
    }
    if set(source) != set(repaired):
        return False
    for key, value in source.items():
        if key in allowed_namespaced_fields:
            source_values = _string_list(value)
            repaired_values = _string_list(repaired.get(key))
            if len(source_values) != len(repaired_values) or any(
                not _is_namespaced_value(source_value, repaired_value)
                for source_value, repaired_value in zip(
                    source_values,
                    repaired_values,
                    strict=True,
                )
            ):
                return False
        elif repaired.get(key) != value:
            return False
    return True


def _id_registry_change_is_namespacing_only(
    name: str,
    *,
    source_path: Path,
    repaired_path: Path,
    source_artifact_rows: list[ArtifactRow] | None = None,
    repaired_artifact_rows: list[ArtifactRow] | None = None,
) -> bool:
    """Allow only the deterministic cross-record ID rewrite in ID registries.

    ``id_registry`` is a derived index over the bundle's artifacts.  Repair
    namespaces brain record IDs and must reflect that in registry rows, but it
    must not add/remove registry entries or alter their type, status, ticker,
    or other metadata.  Compare the parsed rows rather than trusting the
    registry's own hashes.
    """

    source_rows = [
        row.row
        for row in (source_artifact_rows or artifact_rows(source_path))
        if row.canonical_name == name
    ]
    repaired_rows = [
        row.row
        for row in (repaired_artifact_rows or artifact_rows(repaired_path))
        if row.canonical_name == name
    ]

    def row_equivalent(before: dict[str, Any], after: dict[str, Any]) -> bool:
        if set(before) != set(after):
            return False
        for key, value in before.items():
            if key != "record_id" and after.get(key) != value:
                return False
        old_record_id = before.get("record_id")
        new_record_id = after.get("record_id")
        return old_record_id == new_record_id or _is_namespaced_value(old_record_id, new_record_id)

    if name.endswith(".jsonl"):
        return len(source_rows) == len(repaired_rows) and all(
            row_equivalent(before, after)
            for before, after in zip(source_rows, repaired_rows, strict=True)
        )

    if len(source_rows) != 1 or len(repaired_rows) != 1:
        return False
    source = source_rows[0]
    repaired = repaired_rows[0]
    if set(source) != set(repaired):
        return False
    if "records" not in source and "brain_delta_record_ids" in source:
        source_ids = source.get("brain_delta_record_ids")
        repaired_ids = repaired.get("brain_delta_record_ids")
        if not isinstance(source_ids, list) or not isinstance(repaired_ids, list):
            return False
        if len(source_ids) != len(repaired_ids):
            return False
        for key, value in source.items():
            if key == "brain_delta_record_ids":
                continue
            if repaired.get(key) != value:
                return False
        return all(
            isinstance(before_id, str)
            and isinstance(after_id, str)
            and _is_namespaced_value(before_id, after_id)
            for before_id, after_id in zip(source_ids, repaired_ids, strict=True)
        )
    if source.get("records") is None:
        source_id_sets = source.get("id_sets")
        repaired_id_sets = repaired.get("id_sets")
        if not isinstance(source_id_sets, dict) or not isinstance(repaired_id_sets, dict):
            return False
        if set(source_id_sets) != set(repaired_id_sets):
            return False
        for key, value in source_id_sets.items():
            if key == "brain_record_ids":
                source_values = _string_list(value)
                repaired_values = _string_list(repaired_id_sets.get(key))
                if len(source_values) != len(repaired_values) or any(
                    not _is_namespaced_value(source_value, repaired_value)
                    for source_value, repaired_value in zip(
                        source_values,
                        repaired_values,
                        strict=True,
                    )
                ):
                    return False
            elif repaired_id_sets.get(key) != value:
                return False
        return all(
            not (key != "id_sets" and repaired.get(key) != value)
            for key, value in source.items()
        )
    if any(
        key != "records" and source.get(key) != repaired.get(key)
        for key in source
    ):
        return False
    source_records = source.get("records")
    repaired_records = repaired.get("records")
    if not isinstance(source_records, list) or not isinstance(repaired_records, list):
        return False
    return len(source_records) == len(repaired_records) and all(
        isinstance(before, dict)
        and isinstance(after, dict)
        and row_equivalent(before, after)
        for before, after in zip(source_records, repaired_records, strict=True)
    )


def _population_audit(
    rows: list[ArtifactRow],
    *,
    present_artifact_names: set[str] | None = None,
) -> dict[str, Any]:
    by_name = _rows_by_name(rows)
    present = set(by_name) | set(present_artifact_names or ())
    missing_required_blocks = sorted(_CURRENT_GOLD_REQUIRED_BLOCKS - present)
    rules: dict[str, dict[str, Any]] = {}

    source_rows = by_name.get("source_ledger.jsonl", [])
    dispositions = by_name.get("row_disposition.jsonl", [])
    verified_source_aliases = _verified_news_source_aliases(
        row.row for row in source_rows
    )
    source_aliases = _alias_graph(
        [*source_rows, *dispositions],
        fields=_SOURCE_ID_FIELDS,
        verified_links=verified_source_aliases,
    )
    non_descriptor_sources = [row for row in source_rows if not _is_aggregate_news_source(row.row)]
    source_ids_all = _logical_alias_keys(
        non_descriptor_sources,
        aliases=source_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    source_ids = _logical_alias_keys(
        [row for row in source_rows if _is_news_source_row(row.row)],
        aliases=source_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    if not source_ids:
        source_ids = source_ids_all
    disposition_source_ids = _logical_alias_keys(
        dispositions,
        aliases=source_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    _add_exact_rule(rules, "source_to_disposition", source_ids, disposition_source_ids)

    queue_rows = by_name.get("material_review_queue.jsonl", [])
    review_rows = by_name.get("material_review.jsonl", [])
    if not review_rows:
        # Some research sessions embed the completed review object directly in
        # the queue artifact and omit a separate material_review block.  The
        # queue row still carries a stable review id and reviewed decision, so
        # use that existing evidence rather than declaring every screening
        # row missing.
        review_rows = [
            row
            for row in queue_rows
            if _first(row.row, "material_review_id", "review_id") is not None
            and (
                row.row.get("material_reviewed") is True
                or _first(row.row, "review_decision", "review_status") is not None
            )
        ]
    if not queue_rows and review_rows:
        # Legacy sessions sometimes emit only material_review.jsonl.  Those
        # rows already contain source identity and reviewed decisions, so they
        # can verify queue coverage without inventing a second artifact.  The
        # missing queue block remains visible through the current-contract gate.
        queue_rows = review_rows
    # Queue/review artifacts may use a different legacy spelling for the same
    # news row (for example ``NEWS-000001`` versus
    # ``SRC-NEWS-ROW-000001``). Build this graph after both relation artifacts
    # are known; the source/disposition graph cannot see review-only aliases.
    material_aliases = _alias_graph(
        [*source_rows, *dispositions, *queue_rows, *review_rows],
        fields=_SOURCE_ID_FIELDS,
        verified_links=verified_source_aliases,
    )
    disposition_source_ids = _logical_alias_keys(
        dispositions,
        aliases=material_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    expected_material_sources = _logical_alias_keys(
        [row for row in dispositions if _row_requires_material_review(row.row)],
        aliases=material_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    queue_source_ids = _logical_alias_keys(
        queue_rows,
        aliases=material_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    # The queue may intentionally contain additional negative/context reviews.
    # The hard contract is that every material disposition reaches the queue;
    # downstream queue-to-review equality still requires every queued row to be
    # reviewed. Treating these useful extra reviews as population loss rejects
    # otherwise complete bundles.
    _add_subset_rule(
        rules,
        "disposition_to_material_queue",
        expected_material_sources,
        queue_source_ids,
    )
    _add_subset_rule(
        rules,
        "material_queue_to_disposition",
        queue_source_ids,
        disposition_source_ids,
    )
    reviewed_source_ids = _logical_alias_keys(
        review_rows,
        aliases=material_aliases,
        fields=_SOURCE_ID_FIELDS,
    )
    # A bundle may retain additional reviewed observations that were not
    # promoted into the queue. They remain useful audit context; the hard
    # direction is that every queued source has a reviewed row.
    _add_subset_rule(
        rules,
        "material_queue_to_review",
        queue_source_ids,
        reviewed_source_ids,
    )
    reviewed_ids = _keys(review_rows, "material_review_id", "review_id")
    screening_rows = by_name.get("candidate_screening.jsonl", [])
    relation_aliases = _alias_graph(
        [*source_rows, *dispositions, *queue_rows, *review_rows, *screening_rows],
        fields=_SOURCE_ID_FIELDS,
        verified_links=verified_source_aliases,
    )
    review_ids_by_source: dict[str, set[str]] = defaultdict(set)
    for review_row in review_rows:
        review_id = _first(review_row.row, "material_review_id", "review_id")
        if review_id is None:
            continue
        for source_value in _field_string_values(
            review_row.row,
            *_SOURCE_ID_FIELDS,
            "source_ids",
            "source_row_ids",
        ):
            review_ids_by_source[relation_aliases.get(source_value, source_value)].add(review_id)
    screening_review_ids = {
        value
        for row in screening_rows
        for value in _field_string_values(
            row.row,
            "material_review_id",
            "material_review_ids",
            "source_material_review_ids",
            "review_id",
            "review_ids",
            "source_review_id",
            "source_review_ids",
        )
    }
    review_ids_by_observation: dict[str, set[str]] = defaultdict(set)
    for review_row in review_rows:
        review_id = _first(review_row.row, "material_review_id", "review_id")
        if review_id is None:
            continue
        for observation_id in _field_string_values(
            review_row.row,
            "observation_id",
            "observation_ids",
        ):
            review_ids_by_observation[observation_id].add(review_id)
    for screening_row in screening_rows:
        for observation_id in _field_string_values(
            screening_row.row,
            "source_observation_ids",
            "observation_ids",
        ):
            if observation_id in reviewed_ids:
                screening_review_ids.add(observation_id)
            else:
                screening_review_ids.update(review_ids_by_observation.get(observation_id, set()))
        for source_value in _field_string_values(
            screening_row.row,
            *_SOURCE_ID_FIELDS,
            "source_ids",
            "source_row_ids",
        ):
            screening_review_ids.update(
                review_ids_by_source.get(
                    relation_aliases.get(source_value, source_value),
                    set(),
                )
            )
    # Newer bundles keep the screening's observation link on the witness and
    # keep the material review's source link on the review row.  Join through
    # that source alias graph instead of requiring candidate_screening to
    # repeat material_review_id explicitly.
    for witness_row in by_name.get("candidate_semantic_witness.jsonl", []):
        witness_screening_id = _first(
            witness_row.row,
            "screening_id",
            "source_screening_id",
        )
        if witness_screening_id is None:
            continue
        for source_value in _field_string_values(
            witness_row.row,
            "source_id",
            "source_ids",
            "source_row_id",
            "source_row_ids",
        ):
            screening_review_ids.update(
                review_ids_by_source.get(
                    relation_aliases.get(source_value, source_value),
                    set(),
                )
            )
    # Screening rows may retain an audit-only ``MREV-*`` token even when no
    # material_review row exists for that source.  It is useful preserved raw
    # context, but it is not a queue/review FK and must not inflate the
    # material-review population.  Only IDs proven by the review artifact are
    # part of this relation.
    screening_review_ids.intersection_update(reviewed_ids)
    # A material review is allowed to remain a rejected/audit-only observation
    # and therefore need not become a candidate screening row. The complete
    # relation is the other direction: every review ID referenced by a
    # screening row must resolve to an actual reviewed artifact.
    _add_subset_rule(
        rules,
        "material_review_to_screening",
        screening_review_ids,
        reviewed_ids,
    )
    screening_ids = {
        screening_id
        for row in screening_rows
        for screening_id in [_screening_identity(row.row)]
        if screening_id is not None
    }
    rankable_rows = [
        row
        for row in screening_rows
        if (
            str(row.row.get("screening_decision") or "").upper()
            in _RANKABLE_SCREENING_DECISIONS
            or row.row.get("rankable") is True
        )
    ]
    rankable_ids = {
        screening_id
        for row in rankable_rows
        for screening_id in [_screening_identity(row.row)]
        if screening_id is not None
    }
    ranking_rows = by_name.get("candidate_ranking_audit.jsonl", [])
    witness_rows = by_name.get("candidate_semantic_witness.jsonl", [])
    final_witness_rows = by_name.get("final_evidence_witness.jsonl", [])
    final_candidate_ids = {
        candidate_id
        for row in ranking_rows
        if row.row.get("included_in_final") is True
        for candidate_id in [_first(row.row, "candidate_id")]
        if candidate_id is not None
    }
    witness_candidate_ids = _keys(witness_rows, "candidate_id")
    # Some writers intentionally serialize the final-evidence artifact twice:
    # once under the historical candidate-witness filename and once under the
    # final-witness filename. Treat it as final-only only when the two canonical
    # multisets and the independently declared final candidate set are exact.
    final_only_witness_artifact = (
        bool(witness_rows)
        and Counter(canonical_json(row.row) for row in witness_rows)
        == Counter(canonical_json(row.row) for row in final_witness_rows)
        and bool(final_candidate_ids)
        and witness_candidate_ids == final_candidate_ids
    )
    witness_required_rows = [
        row
        for row in rankable_rows
        if (
            not final_only_witness_artifact
            or _first(row.row, "candidate_id") in final_candidate_ids
        )
        and not (
            row.row.get("record_type") == "material_observation_screening"
            and str(row.row.get("screening_decision") or "").upper()
            == "WATCH_SECONDARY"
            and _first(
                row.row,
                "rejection_reason",
                "screening_exclusion_reason",
                "exclusion_reason",
            )
            is not None
            and not _field_string_values(
                row.row,
                "source_inference_id",
                "source_inference_ids",
                "inference_id",
                "inference_ids",
            )
        )
    ]
    witness_required_ids = {
        screening_id
        for row in witness_required_rows
        for screening_id in [_screening_identity(row.row)]
        if screening_id is not None
    }
    screening_identity_ids_by_candidate: dict[str, set[str]] = defaultdict(set)
    for screening_row in screening_rows:
        screening_id = _screening_identity(screening_row.row)
        candidate_id = _first(screening_row.row, "candidate_id")
        if screening_id is not None and candidate_id is not None:
            screening_identity_ids_by_candidate[candidate_id].add(screening_id)
    witness_relation_index: dict[str, set[str]] = defaultdict(set)
    witness_joined_ids: set[str] = set()
    for witness_row in witness_rows:
        identity_values = _field_string_values(
            witness_row.row,
            "screening_id",
            "source_screening_id",
            "candidate_id",
        )
        resolved_screening_ids: set[str] = set()
        for identity in identity_values:
            if identity in screening_ids:
                resolved_screening_ids.add(identity)
            resolved_screening_ids.update(
                screening_identity_ids_by_candidate.get(identity, set())
            )
        if resolved_screening_ids:
            witness_joined_ids.update(resolved_screening_ids)
        elif identity_values:
            # Keep an explicit but unresolved witness identity visible as an
            # extra population key instead of silently discarding it.
            witness_joined_ids.update(identity_values)
        relation_targets = set(resolved_screening_ids)
        if not relation_targets:
            witness_key = _first(
                witness_row.row,
                "candidate_semantic_witness_id",
                "semantic_witness_id",
            )
            if witness_key is not None:
                relation_targets.add(witness_key)
        for relation in _field_string_values(
            witness_row.row,
            "material_review_id",
            "material_review_ids",
            "review_id",
            "review_ids",
            "observation_id",
            "observation_ids",
            "source_row_id",
            "source_row_ids",
            "source_id",
            "source_ids",
        ):
            witness_relation_index[relation].update(relation_targets)
    for screening_row in witness_required_rows:
        joined_screening_id = _screening_identity(screening_row.row)
        if joined_screening_id is None:
            continue
        if joined_screening_id in witness_joined_ids:
            continue
        screening_relations = _field_string_values(
            screening_row.row,
            "material_review_id",
            "material_review_ids",
            "review_id",
            "review_ids",
            "source_observation_ids",
            "observation_id",
            "observation_ids",
            "row_id",
            "news_row_id",
            "source_row_id",
            "source_row_ids",
            "source_id",
            "source_ids",
        )
        if any(relation in witness_relation_index for relation in screening_relations):
            witness_joined_ids.add(joined_screening_id)
    # Semantic witnesses are required for the positive/rankable lane. Some
    # normalized bundles retain WATCH_SECONDARY observations as rankable input
    # to the ranking audit while explicitly rejecting them as audit-only and
    # emitting no inference. Preserve those observations and their ranking rows,
    # but do not misclassify the intentionally unwitnessed audit lane as loss.
    _add_subset_rule(
        rules,
        "screening_to_candidate_witness",
        witness_required_ids,
        witness_joined_ids,
    )
    screenings_by_id = {
        screening_id: row.row
        for row in screening_rows
        for screening_id in [_screening_identity(row.row)]
        if screening_id is not None
    }
    ranking_ids: set[str] = set()
    for row in ranking_rows:
        screening_values = _field_string_values(
            row.row,
            "candidate_screening_id",
            "candidate_screening_ids",
            "source_screening_id",
            "screening_id",
            "source_screening_ids",
            "screening_record_ids",
        )
        if screening_values:
            ranking_ids.update(screening_values)
            continue
        candidate_id = _first(row.row, "candidate_id")
        if candidate_id is not None:
            ranking_ids.add(candidate_id)
    _add_subset_rule(
        rules,
        "ranking_to_screening",
        ranking_ids,
        ranking_ids & screening_ids,
    )
    # A ranking audit may intentionally retain a semantic rejection or an
    # audit-only row.  The completeness direction is therefore rankable
    # screening -> ranking, not ranking -> rankable screening.  Ranking is
    # also allowed to collapse duplicate events for the same ticker; compare
    # the logical population key rather than demanding one row per screening
    # event.
    rankable_population_keys = {
        population_key
        for row in rankable_rows
        for population_key in [_screening_population_key(row.row)]
        if population_key is not None
    }
    ranking_population_keys = {
        population_key
        for screening_id in ranking_ids
        for screening in [screenings_by_id.get(screening_id)]
        for population_key in [_screening_population_key(screening or {})]
        if population_key is not None
    }
    _add_subset_rule(
        rules,
        "ranking_to_rankable_screening",
        rankable_population_keys,
        ranking_population_keys,
    )
    expected_ranking_population = {
        population_key
        for row in rankable_rows
        for population_key in [_screening_population_key(row.row)]
        if population_key is not None
    }
    actual_ranking_population: set[str] = set()
    ranking_consistency_failures: set[str] = set()
    screening_ids_by_candidate: dict[str, list[str]] = defaultdict(list)
    screening_ids_by_ticker: dict[str, list[str]] = defaultdict(list)
    for screening_key, resolved_screening_row in screenings_by_id.items():
        candidate_id = _first(resolved_screening_row, "candidate_id")
        if candidate_id is not None:
            screening_ids_by_candidate[candidate_id].append(screening_key)
        ticker = _first(resolved_screening_row, "ticker", "code")
        if ticker is not None:
            screening_ids_by_ticker[ticker].append(screening_key)
    for row in ranking_rows:
        screening_values = _field_string_values(
            row.row,
            "candidate_screening_id",
            "candidate_screening_ids",
            "source_screening_id",
            "screening_id",
            "source_screening_ids",
            "screening_record_ids",
        )
        if not screening_values:
            candidate_id = _first(row.row, "candidate_id")
            candidate_matches = screening_ids_by_candidate.get(candidate_id or "", [])
            if len(candidate_matches) == 1:
                screening_values = {candidate_matches[0]}
            else:
                ticker = _first(row.row, "ticker", "code")
                ticker_matches = screening_ids_by_ticker.get(ticker or "", [])
                if len(ticker_matches) == 1:
                    screening_values = {ticker_matches[0]}
        for screening_id in screening_values:
            screening: dict[str, Any] | None = screenings_by_id.get(screening_id)
            if screening is None or screening_id not in rankable_ids:
                continue
            expected_candidate_id = _first(screening, "candidate_id")
            actual_candidate_id = _first(row.row, "candidate_id")
            expected_ticker = _first(screening, "ticker", "code")
            actual_ticker = _first(row.row, "ticker", "code")
            if expected_candidate_id is not None and actual_candidate_id != expected_candidate_id:
                ranking_consistency_failures.add(f"{screening_id}:candidate_id")
                continue
            if expected_ticker is not None and actual_ticker is not None and actual_ticker != expected_ticker:
                ranking_consistency_failures.add(f"{screening_id}:ticker")
                continue
            population_key = _screening_population_key(screening)
            if population_key is not None:
                actual_ranking_population.add(population_key)
    _add_exact_rule(
        rules,
        "rankable_to_ranking_audit",
        expected_ranking_population,
        actual_ranking_population,
    )
    _add_exact_rule(
        rules,
        "ranking_candidate_consistency",
        set(),
        ranking_consistency_failures,
    )

    final_candidates = _final_candidates(by_name.get("blind_prediction.json", []))
    final_ids = {
        candidate_id
        for candidate in final_candidates
        for candidate_id in [_first(candidate, "candidate_id")]
        if candidate_id is not None
    }
    validated_final_ids = _postseal_validated_final_ids(
        by_name,
        sealed_final_ids=final_ids,
    )
    relation_final_ids = validated_final_ids or final_ids
    relation_final_candidates = [
        candidate
        for candidate in final_candidates
        if _first(candidate, "candidate_id") in relation_final_ids
    ]
    final_witness_ids, final_witness_duplicate_count = _resolved_final_relation_ids(
        by_name.get("final_evidence_witness.jsonl", []),
        final_candidates=relation_final_candidates,
    )
    final_semantic_ids, final_semantic_duplicate_count = _resolved_final_relation_ids(
        by_name.get("final_semantic_audit.jsonl", []),
        final_candidates=relation_final_candidates,
        final_witness_rows=by_name.get("final_evidence_witness.jsonl", []),
    )
    _add_exact_rule(
        rules,
        "final_to_evidence_witness",
        relation_final_ids,
        final_witness_ids,
    )
    _add_exact_rule(
        rules,
        "final_to_semantic_audit",
        relation_final_ids,
        final_semantic_ids,
    )

    leader_rows = by_name.get("outcome_leader_census.jsonl", [])
    leader_ids = _keys(leader_rows, "outcome_leader_id", "leader_id", "leader_census_id")
    outcome_rows = by_name.get("outcome_ledger.jsonl", [])
    policy_rows = [
        *by_name.get("postmortem_summary.json", []),
        *by_name.get("outcome_population_audit.json", []),
    ]
    (
        amount_top_n,
        turnover_top_n,
        high_return_threshold,
        high_return_rank_top_n,
    ) = _leader_policy_thresholds(
        leader_rows,
        policy_rows=policy_rows,
    )
    separately_quarantined_tickers: set[str] = set()
    for policy_row in policy_rows:
        quarantine_census = policy_row.row.get("quarantine_census")
        if not isinstance(quarantine_census, list):
            continue
        for quarantine_row in quarantine_census:
            if not isinstance(quarantine_row, dict):
                continue
            ticker = _outcome_ticker(quarantine_row)
            if ticker is not None:
                separately_quarantined_tickers.add(ticker)
    # Some legacy policies keep training-ineligible liquidity leaders inside
    # the census, while newer bundles list them only in quarantine_census.
    # An explicitly present leader remains part of the population; only the
    # separately listed and absent rows are excluded from the required set.
    actual_leader_tickers = {
        ticker for row in leader_rows for ticker in [_outcome_ticker(row.row)] if ticker is not None
    }
    # A rank-qualified outcome may still be intentionally absent from the
    # leader census when its price label is explicitly quarantined (for
    # example, a new listing with no prior reference price).  Keep such rows
    # in the outcome ledger, but do not call them missing leaders.  An
    # explicitly present leader always remains population evidence, including
    # quarantined liquidity leaders.
    separately_quarantined_tickers.update(
        ticker
        for row in outcome_rows
        for ticker in [_outcome_ticker(row.row)]
        if ticker is not None
        and ticker not in actual_leader_tickers
        and _outcome_row_is_explicitly_quarantined(row.row)
    )
    separately_quarantined_tickers -= actual_leader_tickers
    explicit_policy_flags = [_outcome_policy_leader(row.row) for row in outcome_rows]
    policy_flags_complete = bool(explicit_policy_flags) and all(flag is not None for flag in explicit_policy_flags)
    liquidity_policy_signal = any(
        token in str(leader_row.row.get(field) or {}).upper()
        for leader_row in leader_rows
        for field in _LEADER_MEMBERSHIP_FIELDS
        for token in ("AMOUNT", "TURNOVER")
    )
    liquidity_policy_underspecified_count = int(
        bool(outcome_rows)
        and not policy_flags_complete
        and (amount_top_n is None or turnover_top_n is None)
        and liquidity_policy_signal
    )
    expected_leader_outcome_ids = {
        outcome_id
        for row in outcome_rows
        for outcome_id in [_outcome_identity(row.row)]
        if outcome_id is not None
        and _outcome_ticker(row.row) not in separately_quarantined_tickers
        and _outcome_row_requires_leader(
            row.row,
            amount_top_n=amount_top_n,
            turnover_top_n=turnover_top_n,
            high_return_threshold=high_return_threshold,
            high_return_rank_top_n=high_return_rank_top_n,
        )
    }
    # A few legacy bundles intentionally include a non-metric leader row for
    # a final-watchlist/outcome join.  The explicit cohort token is population
    # evidence even when the outcome metrics do not meet the leader policy;
    # do not invent a metric threshold for it.
    expected_leader_outcome_ids.update(
        outcome_id
        for row in leader_rows
        for outcome_id in [_outcome_explicit_identity(row.row)]
        if outcome_id is not None and _leader_row_has_explicit_non_metric_membership(row.row)
    )
    outcome_id_by_ticker = {
        ticker: outcome_id
        for row in outcome_rows
        for ticker in [_outcome_ticker(row.row)]
        for outcome_id in [_outcome_identity(row.row)]
        if ticker is not None and outcome_id is not None
    }
    # Some legacy leader censuses identify liquidity members only by ticker
    # and ``outcome_class=LIQUIDITY_TOP_GROUP``.  Resolve that explicit
    # membership through the outcome ledger rather than inventing a rank
    # threshold or treating the row as an unexplained extra.
    expected_leader_outcome_ids.update(
        resolved_id
        for row in leader_rows
        if _leader_row_has_explicit_non_metric_membership(row.row)
        for resolved_id in [
            _outcome_explicit_identity(row.row)
            or outcome_id_by_ticker.get(_outcome_ticker(row.row) or "")
        ]
        if resolved_id is not None
    )
    actual_leader_outcome_ids = {
        outcome_id
        for row in leader_rows
        for outcome_id in [
            _outcome_explicit_identity(row.row)
            or outcome_id_by_ticker.get(_outcome_ticker(row.row) or "")
            or _outcome_identity(row.row)
            or f"UNRESOLVED_LEADER:{row.origin_key}"
        ]
    }
    _add_exact_rule(
        rules,
        "outcome_to_leader_census",
        expected_leader_outcome_ids,
        actual_leader_outcome_ids,
    )
    reverse_ids = _keys(
        by_name.get("outcome_to_news_audit.jsonl", []),
        "outcome_leader_id",
        "leader_id",
        "leader_census_id",
    )
    _add_exact_rule(rules, "leader_to_reverse_audit", leader_ids, reverse_ids)

    brain_rows = by_name.get("brain_delta.jsonl", [])
    brain_ids = {
        key
        for row in brain_rows
        for key in [_stable_brain_record_key(row.row)]
        if key is not None
    }
    closure_ids = {
        key
        for row in by_name.get("record_provenance_closure_audit.jsonl", [])
        for key in [_stable_brain_record_key(row.row)]
        if key is not None
    }
    _add_exact_rule(rules, "brain_to_provenance_closure", brain_ids, closure_ids)

    required_by_type: dict[str, list[str]] = {}
    actual_by_type: dict[str, list[str]] = {}
    brain_dicts = [row.row for row in brain_rows]
    for block_name, (category, identifier_field) in _CASE_BLOCKS.items():
        identifier_fields = _CASE_ID_ALIASES.get(
            block_name,
            (identifier_field,),
        )
        required = _keys(by_name.get(block_name, []), *identifier_fields)
        actual: set[str] = set()
        for artifact_row in by_name.get(block_name, []):
            case = artifact_row.row
            case_id = _first(case, *identifier_fields)
            if case_id is None:
                continue
            allowed_types = _case_population_record_types_for_validation(
                block_name,
                case,
                canonical_record_type=_CASE_RECORD_TYPES[category],
            )
            if any(
                record.get("record_type") in allowed_types
                and (
                    _case_population_identity_matches(
                        category,
                        case_id,
                        _case_population_record_ids(record, category, identifier_fields),
                    )
                    or (
                        block_name in {"direct_event_cases.jsonl", "issuer_day_cases.jsonl"}
                        and record.get("record_type") in allowed_types
                        and (
                            record.get("record_type") == "supervised_direct_event_case"
                            or block_name == "direct_event_cases.jsonl"
                        )
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=True,
                        )
                        is not None
                    )
                    or (
                        block_name == "theme_formation_cases.jsonl"
                        and record.get("record_type")
                        == "context_market_state_or_fact_case"
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=False,
                        )
                        is not None
                    )
                    or (
                        block_name == "newsless_or_unexplained_cases.jsonl"
                        and record.get("record_type")
                        == "entity_resolution_error_case"
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=False,
                        )
                        is not None
                    )
                    or (
                        block_name == "newsless_or_unexplained_cases.jsonl"
                        and record.get("record_type")
                        == "candidate_generation_error_case"
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=False,
                        )
                        is not None
                    )
                    or (
                        block_name == "beneficiary_discovery_cases.jsonl"
                        and record.get("record_type") in allowed_types
                        and record.get("record_type") != "beneficiary_discovery_case"
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=False,
                        )
                        is not None
                    )
                    or (
                        block_name
                        in {
                            "issuer_day_cases.jsonl",
                            "direct_event_cases.jsonl",
                            "candidate_generation_error_cases.jsonl",
                            "newsless_or_unexplained_cases.jsonl",
                            "negative_control_cases.jsonl",
                            "context_market_state_or_fact_cases.jsonl",
                        }
                        and record.get("record_type") in allowed_types
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=block_name
                            in {"issuer_day_cases.jsonl", "direct_event_cases.jsonl"},
                        )
                        is not None
                    )
                    or (
                        block_name == "direct_event_cases.jsonl"
                        and case.get("direct_event_eligible") is False
                        and case.get("training_eligible") is False
                        and record.get("record_type") in allowed_types
                        and _case_population_join_evidence_for_validation(
                            case,
                            record,
                            allow_partial_case_facts=False,
                        )
                        is not None
                    )
                )
                for record in brain_dicts
            ):
                actual.add(case_id)
        required_by_type[category] = sorted(required)
        actual_by_type[category] = sorted(actual)
        _add_subset_rule(rules, f"case_to_brain:{category}", required, actual)

    population_underfill_count = sum(len(rule["missing_keys"]) for rule in rules.values())
    population_extra_count = sum(len(rule["extra_keys"]) for rule in rules.values() if rule["mode"] == "EXACT")
    duplicate_logical_key_count = sum(
        _duplicate_alias_key_count(
            by_name.get(block_name, []),
            _CASE_ID_ALIASES.get(block_name, (identifier_field,)),
        )
        for block_name, (_, identifier_field) in _CASE_BLOCKS.items()
        if block_name != "negative_control_cases.jsonl"
    )
    duplicate_logical_key_count += _negative_control_duplicate_key_count(
        by_name.get("negative_control_cases.jsonl", [])
    )
    duplicate_logical_key_count += sum(
        _duplicate_alias_key_count(by_name.get(block_name, []), fields)
        for block_name, fields in _ARTIFACT_PRIMARY_KEYS.items()
    )
    duplicate_logical_key_count += sum(
        _duplicate_alias_key_count(by_name.get(block_name, []), fields)
        for block_name, fields in _ARTIFACT_ADDITIONAL_UNIQUE_KEYS.items()
    )
    duplicate_logical_key_count += _final_watchlist_duplicate_count(by_name.get("blind_prediction.json", []))
    duplicate_logical_key_count += final_witness_duplicate_count + final_semantic_duplicate_count
    return {
        "current_contract_blocks_present": not missing_required_blocks,
        "missing_current_contract_blocks": missing_required_blocks,
        "rules": rules,
        "required_keys_by_type": required_by_type,
        "actual_keys_by_type": actual_by_type,
        "population_underfill_count": population_underfill_count,
        "population_extra_count": population_extra_count,
        "duplicate_logical_key_count": duplicate_logical_key_count,
        "liquidity_policy_underspecified_count": (liquidity_policy_underspecified_count),
        "leader_amount_top_n": amount_top_n,
        "leader_turnover_top_n": turnover_top_n,
        "leader_high_return_threshold": high_return_threshold,
        "leader_high_return_rank_top_n": high_return_rank_top_n,
        "leader_separate_quarantine_count": len(separately_quarantined_tickers),
        "declared_population_manifest_complete": _declared_population_manifest_complete(
            by_name
        ),
        "nontraining_case_underfill_only": _nontraining_case_underfill_only(
            by_name,
            rules,
        ),
    }


def _combined_population(
    source: dict[str, Any],
    repaired: dict[str, Any],
) -> dict[str, Any]:
    legacy_contract_population_quarantine = _legacy_contract_population_quarantine(
        source,
        repaired,
    )
    return {
        "source": source,
        "repaired": repaired,
        "current_contract_blocks_present": repaired["current_contract_blocks_present"],
        "population_underfill_count": repaired["population_underfill_count"],
        "population_extra_count": repaired["population_extra_count"],
        "duplicate_logical_key_count": repaired["duplicate_logical_key_count"],
        "liquidity_policy_underspecified_count": repaired["liquidity_policy_underspecified_count"],
        "source_population_underfill_count": source["population_underfill_count"],
        "source_population_extra_count": source["population_extra_count"],
        "source_duplicate_logical_key_count": source["duplicate_logical_key_count"],
        "legacy_contract_population_quarantine": legacy_contract_population_quarantine,
    }


def _legacy_contract_population_quarantine(
    source: dict[str, Any],
    repaired: dict[str, Any],
) -> bool:
    """Separate legacy contract shape from repair-induced population loss.

    Older bundles can omit the current case-artifact blocks and consequently
    fail relation expectations that were introduced later.  We may treat that
    mismatch as importable legacy material only when the source and repaired
    audits have identical relation cardinalities and no duplicate keys.  This
    never masks a source/repaired difference, a dropped artifact, or a record
    lineage/provenance failure; those remain hard blockers elsewhere.
    """

    if source.get("current_contract_blocks_present") is True:
        return False
    if repaired.get("current_contract_blocks_present") is True:
        return False
    if source.get("duplicate_logical_key_count") != 0:
        return False
    if repaired.get("duplicate_logical_key_count") != 0:
        return False
    if (
        source.get("nontraining_case_underfill_only") is True
        and repaired.get("nontraining_case_underfill_only") is True
    ):
        # Some legacy bundles intentionally keep excluded case artifacts in
        # their machine blocks without mirroring each one into brain_delta.
        # The case rows are still preserved in the imported raw bundle; only
        # explicit zero-weight/non-training rows may use this quarantine.
        return True
    for field in ("population_extra_count", "liquidity_policy_underspecified_count"):
        if source.get(field) != repaired.get(field):
            return False
    source_rules = _population_rule_cardinalities(source.get("rules"))
    repaired_rules = _population_rule_cardinalities(repaired.get("rules"))
    changed_rules = {
        name
        for name in set(source_rules) | set(repaired_rules)
        if source_rules.get(name) != repaired_rules.get(name)
    }

    def allowed_derived_population_change(name: str) -> bool:
        if name.startswith("case_to_brain:"):
            return True
        if name != "brain_to_provenance_closure":
            return False
        source_rule = (source.get("rules") or {}).get(name, {})
        repaired_rule = (repaired.get("rules") or {}).get(name, {})
        return all(
            rule.get("mode") == "EXACT"
            and not rule.get("missing_keys")
            and not rule.get("extra_keys")
            and rule.get("expected_count") == rule.get("actual_count")
            for rule in (source_rule, repaired_rule)
        )

    underfill_equal = source.get("population_underfill_count") == repaired.get(
        "population_underfill_count"
    )
    if (
        underfill_equal
        and changed_rules
        and all(allowed_derived_population_change(name) for name in changed_rules)
    ):
        # Exact case materialization grows brain_delta and its closure artifact
        # together. Both population graphs remain closed; lineage and derived-
        # case audits separately prove every added row against a source hash.
        return True
    if not underfill_equal:
        # Repair may only expose case-to-brain aliases that the legacy source
        # already contains.  All source/disposition/ranking/leader relations
        # must remain cardinality-identical; the actual record lineage and
        # artifact audit separately prove that no case was invented or lost.
        if (
            source.get("population_underfill_count", 0)
            >= repaired.get("population_underfill_count", 0)
            and changed_rules
            and all(allowed_derived_population_change(name) for name in changed_rules)
            and all(
                source_rules.get(name) == repaired_rules.get(name)
                for name in set(source_rules) | set(repaired_rules)
                if not allowed_derived_population_change(name)
            )
        ):
            return True
        if not (
            source.get("declared_population_manifest_complete") is True
            and repaired.get("declared_population_manifest_complete") is True
        ):
            return False
    if source_rules == repaired_rules:
        return bool(source_rules)
    # A legacy bundle can be internally complete according to its own
    # population manifest while the current validator sees case IDs through
    # newer alias lanes.  Allow only case-to-brain cardinality differences;
    # every source/disposition/ranking/leader relation must remain identical.
    if not (
        source.get("declared_population_manifest_complete") is True
        and repaired.get("declared_population_manifest_complete") is True
    ):
        return False
    for name, source_cardinality in source_rules.items():
        if name.startswith("case_to_brain:"):
            continue
        if repaired_rules.get(name) != source_cardinality:
            return False
    return all(
        name.startswith("case_to_brain:")
        for name in repaired_rules
        if name not in source_rules
    )


def _nontraining_case_underfill_only(
    by_name: dict[str, list[ArtifactRow]],
    rules: dict[str, dict[str, Any]],
) -> bool:
    """Allow legacy case rows omitted from brain_delta only when quarantined.

    This is deliberately narrow: every missing case must be explicitly
    non-training, zero-weight, and carry an exclusion/no-bridge reason.  We do
    not manufacture a brain record or treat an unexplained missing positive
    case as harmless.
    """

    missing_ids: list[tuple[str, str]] = []
    for rule_name, rule in rules.items():
        if not rule_name.startswith("case_to_brain:"):
            continue
        for key in rule.get("missing_keys", []):
            if isinstance(key, str):
                missing_ids.append((rule_name.split(":", 1)[1], key))
    if not missing_ids:
        return False
    case_rows_by_id: dict[str, dict[str, Any]] = {}
    for block_name, aliases in _CASE_ID_ALIASES.items():
        for artifact in by_name.get(block_name, []):
            case_id = _first(artifact.row, *aliases)
            if case_id is not None:
                case_rows_by_id[str(case_id)] = artifact.row
    for _, case_id in missing_ids:
        case = case_rows_by_id.get(case_id)
        if case is None:
            return False
        if case.get("training_eligible") is not False:
            return False
        if abs(_float(case.get("sample_weight"))) > 0.000001:
            return False
        reason = str(
            case.get("training_exclusion_reason")
            or case.get("no_bridge_reason")
            or case.get("exclusion_reason")
            or ""
        ).lower()
        if not reason or not any(
            token in reason
            for token in (
                "semantic_contract_failed",
                "blind_packet",
                "preseal",
                "outcome_access_order",
                "no_bridge",
                "not_training",
                "excluded",
                "quarantine",
            )
        ):
            return False
        decision = str(case.get("screening_decision") or "").upper()
        if decision and decision not in {
            "EXCLUDE",
            "WATCH",
            "WATCH_SECONDARY",
            "AUDIT_ONLY",
        }:
            return False
    return True


def _declared_population_manifest_complete(
    by_name: dict[str, list[ArtifactRow]],
) -> bool:
    """Recognize a source-declared complete legacy population conservatively."""

    for block_name in ("validation_report.json", "postmortem_summary.json"):
        for artifact in by_name.get(block_name, []):
            row = artifact.row
            manifest = row.get("brain_delta_population_manifest")
            if not isinstance(manifest, dict):
                continue
            if isinstance(row.get("fatal_blockers"), list) and row["fatal_blockers"]:
                continue
            underfilled_count = _int(manifest.get("underfilled_count"), default=0)
            underfilled_types = manifest.get("underfilled_record_types")
            if underfilled_count != 0:
                continue
            if underfilled_types not in (None, [], {}):
                continue
            actual = _int(
                manifest.get("actual_brain_delta_record_count")
                or manifest.get("brain_delta_record_count"),
                default=0,
            )
            expected_min = _int(manifest.get("expected_brain_delta_min"), default=0)
            if actual <= 0 or (expected_min and actual < expected_min):
                continue
            has_explicit_gap = False
            for key, value in manifest.items():
                normalized = str(key).lower()
                if normalized.endswith("_missing_count") and _int(value, default=0) > 0:
                    has_explicit_gap = True
                    break
                if normalized.endswith("_incomplete_count") and _int(value, default=0) > 0:
                    has_explicit_gap = True
                    break
            if not has_explicit_gap:
                return True
    return False


def _population_rule_cardinalities(value: Any) -> dict[str, tuple[Any, ...]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, tuple[Any, ...]] = {}
    for name, rule in value.items():
        if not isinstance(rule, dict):
            continue
        missing = rule.get("missing_keys")
        extra = rule.get("extra_keys")
        result[str(name)] = (
            rule.get("mode"),
            rule.get("actual_count"),
            rule.get("expected_count"),
            len(missing) if isinstance(missing, list) else None,
            len(extra) if isinstance(extra, list) else None,
        )
    return result


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value.lower())


_SEMANTIC_EXCLUSION_RELATION_FIELDS = (
    "candidate_id",
    "screening_id",
    "source_screening_id",
    "primary_fact_id",
    "fact_id",
    "mechanism_inference_id",
    "inference_id",
)

_SEMANTIC_REQUIRED_TRUE_FIELDS = (
    "local_predicate_owner_is_candidate",
    "issuer_role_anchor_valid",
    "economic_mechanism_supported_by_quote",
    "material_fact_class_allowed_by_quote_role",
    "quote_role_allowed_by_catalyst_type",
    "catalyst_entailment_valid",
    "mechanism_supported",
    "quote_role_catalyst_alignment_valid",
)

_SEMANTIC_NONPOSITIVE_RECORD_TOKENS = (
    "audit",
    "context",
    "counterexample",
    "error",
    "negative",
    "newsless",
)


def semantic_exclusion_relation_ids(
    jsonl_blocks: dict[str, list[dict[str, Any]]],
) -> set[str]:
    """Return explicit relation IDs for positive semantic rows that contradict themselves."""

    relation_ids: set[str] = set()
    for block_name in (
        "candidate_semantic_witness.jsonl",
        "final_evidence_witness.jsonl",
        "final_semantic_audit.jsonl",
    ):
        for row in jsonl_blocks.get(block_name, []):
            if not _semantic_positive_contract_failed(row, block_name=block_name):
                continue
            relation_ids.update(
                value
                for field in _SEMANTIC_EXCLUSION_RELATION_FIELDS
                for value in _field_string_values(row, field)
            )
    return relation_ids


def record_matches_semantic_exclusion(
    record: dict[str, Any],
    relation_ids: set[str],
) -> bool:
    """Match only positive training records; negative/audit records remain useful."""

    if not relation_ids or record.get("training_eligible") is not True:
        return False
    record_kind = " ".join(
        str(value or "").lower()
        for value in (
            record.get("record_type"),
            record.get("training_target"),
            (record.get("payload") or {}).get("training_target")
            if isinstance(record.get("payload"), dict)
            else None,
        )
    )
    if any(token in record_kind for token in _SEMANTIC_NONPOSITIVE_RECORD_TOKENS):
        return False
    return bool(record_semantic_exclusion_relation_ids(record, relation_ids))


def record_semantic_exclusion_relation_ids(
    record: dict[str, Any],
    relation_ids: set[str],
) -> set[str]:
    """Return the exact semantic relation IDs present in one positive record."""

    record_kind = " ".join(
        str(value or "").lower()
        for value in (
            record.get("record_type"),
            record.get("training_target"),
            (record.get("payload") or {}).get("training_target")
            if isinstance(record.get("payload"), dict)
            else None,
        )
    )
    if any(token in record_kind for token in _SEMANTIC_NONPOSITIVE_RECORD_TOKENS):
        return set()
    return _all_nested_string_values(record) & relation_ids


def _semantic_exclusion_audit(
    rows: list[ArtifactRow],
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    by_name = _rows_by_name(rows)
    relation_ids = semantic_exclusion_relation_ids(
        {
            name: [row.row for row in by_name.get(name, [])]
            for name in (
                "candidate_semantic_witness.jsonl",
                "final_evidence_witness.jsonl",
                "final_semantic_audit.jsonl",
            )
        }
    )
    invalid_eligible = [
        _record_id(record)
        for record in records
        if record_matches_semantic_exclusion(record, relation_ids)
    ]
    excluded = [
        _record_id(record)
        for record in records
        if record.get("training_exclusion_reason") == "semantic_contract_failed"
    ]
    return {
        "semantic_exclusion_relation_count": len(relation_ids),
        "semantic_invalid_training_eligible_count": len(invalid_eligible),
        "semantic_invalid_training_eligible_record_ids": [
            value for value in invalid_eligible if value is not None
        ],
        "semantic_excluded_record_count": len(excluded),
        "semantic_excluded_record_ids": [value for value in excluded if value is not None],
    }


def _semantic_positive_contract_failed(
    row: dict[str, Any],
    *,
    block_name: str,
) -> bool:
    verdict = (_semantic_verdict_value(row) or "").upper()
    is_final = block_name in {
        "final_evidence_witness.jsonl",
        "final_semantic_audit.jsonl",
    }
    is_positive = is_final or any(
        row.get(field) is True
        for field in (
            "candidate_generation_eligible",
            "candidate_final_eligible",
            "final_eligible",
            "final_eligible_after_semantic_gate",
            "final_eligible_semantic",
        )
    ) or verdict in {"PASS", "PASSED", "PASS_NONFINAL", "PASS_TO_RANKING"}
    if not is_positive:
        return False
    if verdict and verdict not in {"PASS", "PASSED", "PASS_NONFINAL", "PASS_TO_RANKING"}:
        return True
    if _string_list(row.get("fail_reasons")):
        return True
    if row.get("forbidden_quote_role_detected") is True:
        return True
    return any(row.get(field) is False for field in _SEMANTIC_REQUIRED_TRUE_FIELDS)


def _all_nested_string_values(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, dict):
        return {
            item
            for nested in value.values()
            for item in _all_nested_string_values(nested)
        }
    if isinstance(value, list):
        return {item for nested in value for item in _all_nested_string_values(nested)}
    return set()


def _semantic_audit(rows: list[ArtifactRow]) -> dict[str, Any]:
    by_name = _rows_by_name(rows)
    failures: list[str] = []
    external_quote_verification: list[str] = []
    facts = {
        fact_id: row.row
        for row in by_name.get("fact_ledger_blind.jsonl", [])
        for fact_id in [_string(row.row.get("fact_id"))]
        if fact_id is not None
    }
    sources: dict[str, dict[str, Any]] = {}
    for source_row in by_name.get("source_ledger.jsonl", []):
        for source_id in _field_string_values(
            source_row.row,
            "source_id",
            "source_row_id",
            "row_id",
        ):
            sources[source_id] = source_row.row
    disposition_aliases: dict[str, str] = {}
    for disposition in by_name.get("row_disposition.jsonl", []):
        canonical_source = _first(
            disposition.row,
            "source_row_id",
            "source_id",
        )
        row_id = _first(disposition.row, "row_id")
        if canonical_source is not None and row_id is not None:
            disposition_aliases[row_id] = canonical_source
    screenings = {
        screening_id: row.row
        for row in by_name.get("candidate_screening.jsonl", [])
        for screening_id in [_string(row.row.get("screening_id"))]
        if screening_id is not None
    }
    final_witness_rows = by_name.get("final_evidence_witness.jsonl", [])
    final_witnesses = {
        witness_id: row.row
        for row in final_witness_rows
        for witness_id in [_first(row.row, "final_evidence_witness_id", "witness_id")]
        if witness_id is not None
    }
    final_witness_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for final_witness_row in final_witness_rows:
        final_witness = final_witness_row.row
        candidate_id = _string(final_witness.get("candidate_id"))
        if candidate_id is not None:
            final_witness_candidates[candidate_id].append(final_witness)
    final_semantic_by_candidate = {
        candidate_id: row.row
        for row in by_name.get("final_semantic_audit.jsonl", [])
        for candidate_id in [_string(row.row.get("candidate_id"))]
        if candidate_id is not None
    }
    for block_name in (
        "candidate_semantic_witness.jsonl",
        "final_evidence_witness.jsonl",
        "final_semantic_audit.jsonl",
    ):
        for row in by_name.get(block_name, []):
            semantic_row = row.row
            if block_name == "candidate_semantic_witness.jsonl":
                screening = screenings.get(
                    _first(row.row, "screening_id", "source_screening_id") or "",
                    {},
                )
                candidate_verdict = _semantic_verdict_value(row.row)
                screening_decision = str(
                    row.row.get("screening_decision") or screening.get("screening_decision") or ""
                ).upper()
                candidate_is_negative = (
                    row.row.get("candidate_generation_eligible") is False
                    or row.row.get("accepted_for_rankable_pool") is False
                    or (bool(screening_decision) and screening_decision not in _RANKABLE_SCREENING_DECISIONS)
                    or candidate_verdict
                    in {
                        "REJECTED",
                        "REJECT",
                        "EXCLUDED",
                        "REJECTED_OR_AUDIT",
                    }
                    or _is_negative_semantic_verdict(candidate_verdict)
                )
                if candidate_is_negative:
                    _audit_negative_candidate_witness(
                        row,
                        screening=screening,
                        final_candidate_ids=set(final_witness_candidates),
                        facts=facts,
                        sources=sources,
                        disposition_aliases=disposition_aliases,
                        failures=failures,
                        external_quote_verification=external_quote_verification,
                    )
                    continue
                semantic_row = dict(row.row)
                legacy_verdict = _semantic_verdict_value(row.row)
                if legacy_verdict is not None:
                    semantic_row.setdefault("semantic_verdict", legacy_verdict)
                legacy_fact_id = _semantic_fact_id(row.row)
                if legacy_fact_id is not None:
                    semantic_row.setdefault("primary_fact_id", legacy_fact_id)
                source_quote = _semantic_quote(row.row)
                if source_quote is not None:
                    semantic_row.setdefault("primary_quote", source_quote)
                primary_fact = facts.get(_string(semantic_row.get("primary_fact_id")) or "")
                if primary_fact is not None:
                    fact_quote = _string(primary_fact.get("exact_quote"))
                    if fact_quote is not None and _string(semantic_row.get("primary_quote")) is None:
                        semantic_row["primary_quote"] = fact_quote
                screening_fact_ids = _string_list(screening.get("source_fact_ids"))
                if len(screening_fact_ids) == 1:
                    semantic_row.setdefault(
                        "primary_fact_id",
                        screening_fact_ids[0],
                    )
                if "local_predicate_owner_is_candidate" not in semantic_row:
                    explicit_owner = semantic_row.get("candidate_is_local_predicate_owner")
                    if isinstance(explicit_owner, bool):
                        semantic_row["local_predicate_owner_is_candidate"] = explicit_owner
                    else:
                        owner = _string(semantic_row.get("local_predicate_owner"))
                        company = _string(semantic_row.get("candidate_company") or semantic_row.get("company_name"))
                        if owner is not None and company is not None:
                            semantic_row["local_predicate_owner_is_candidate"] = owner == company
            if block_name == "final_evidence_witness.jsonl":
                candidate_id = _string(row.row.get("candidate_id"))
                semantic_audit = final_semantic_by_candidate.get(candidate_id or "")
                if semantic_audit is not None:
                    semantic_row = dict(row.row)
                    for key, value in semantic_audit.items():
                        semantic_row.setdefault(key, value)
            if block_name == "final_semantic_audit.jsonl":
                witness_id = _first(
                    row.row,
                    "final_evidence_witness_id",
                    "witness_id",
                )
                matched_witness = final_witnesses.get(witness_id or "")
                if matched_witness is None:
                    candidate_id = _string(row.row.get("candidate_id"))
                    candidate_witnesses = final_witness_candidates.get(
                        candidate_id or "",
                        [],
                    )
                    if len(candidate_witnesses) == 1:
                        matched_witness = candidate_witnesses[0]
                if matched_witness is not None:
                    semantic_row = {**matched_witness, **row.row}
            semantic_row = dict(semantic_row)
            semantic_fact_id = _semantic_fact_id(semantic_row)
            if semantic_fact_id is not None:
                semantic_row.setdefault("primary_fact_id", semantic_fact_id)
            semantic_quote = _semantic_quote(semantic_row)
            if semantic_quote is not None:
                semantic_row.setdefault("primary_quote", semantic_quote)
            if _string(semantic_row.get("primary_quote")) is None and semantic_fact_id is not None:
                fact_quote = _string((facts.get(semantic_fact_id) or {}).get("exact_quote"))
                if fact_quote is not None:
                    semantic_row["primary_quote"] = fact_quote
            verdict = _semantic_verdict_value(semantic_row)
            fail_reasons = _string_list(semantic_row.get("fail_reasons"))
            if (
                block_name == "candidate_semantic_witness.jsonl"
                and verdict in {"PASS", "PASSED", "PASS_NONFINAL"}
                and set(fail_reasons) == set(_string_list(screening.get("semantic_risk_flags")))
            ):
                fail_reasons = []
            if verdict not in {"PASS", "PASSED", "PASS_NONFINAL", "PASS_TO_RANKING"} or fail_reasons:
                failures.append(f"{block_name}:{row.origin_key}:verdict")
            if not _string(semantic_row.get("primary_quote")):
                failures.append(f"{block_name}:{row.origin_key}:primary_quote")
            candidate_id = _string(semantic_row.get("candidate_id"))
            requires_positive_binding = block_name != "candidate_semantic_witness.jsonl" or (
                semantic_row.get("final_eligible") is True or candidate_id in final_witness_candidates
            )
            if requires_positive_binding and semantic_row.get("forbidden_quote_role_detected") is True:
                failures.append(f"{block_name}:{row.origin_key}:forbidden_quote_role")
            owner = semantic_row.get("local_predicate_owner_is_candidate")
            if requires_positive_binding and owner is False:
                failures.append(f"{block_name}:{row.origin_key}:predicate_owner")
            _audit_semantic_quote_linkage(
                row,
                semantic_row=semantic_row,
                facts=facts,
                sources=sources,
                disposition_aliases=disposition_aliases,
                failures=failures,
                external_quote_verification=external_quote_verification,
            )

    regressions = by_name.get("semantic_regression_tests.jsonl", [])
    expected_fixture_ids = {f"SEM-{index:03d}" for index in range(1, 14)}
    actual_fixture_ids = _keys(regressions, "fixture_id")
    current_regression_contract_pass = (
        bool(regressions)
        and actual_fixture_ids == expected_fixture_ids
        and all(_current_regression_row_passes(row.row) for row in regressions)
    )
    legacy_regression_profile_valid = bool(regressions) and all(
        _regression_pass_attested(row.row) and _regression_expectations_match(row.row) for row in regressions
    )
    if regressions and not current_regression_contract_pass and not legacy_regression_profile_valid:
        failures.append("semantic_regression_tests:fixture_set")
    for row in regressions:
        if not _regression_pass_attested(row.row):
            failures.append(f"semantic_regression_tests:{row.origin_key}:passed")
        if not _regression_expectations_match(row.row):
            failures.append(f"semantic_regression_tests:{row.origin_key}:verdict")
    return {
        "failure_count": len(failures),
        "failures": failures,
        "external_quote_verification_required_count": len(external_quote_verification),
        "external_quote_verification_samples": external_quote_verification[:50],
        "independent_llm_semantic_review_required": False,
        "current_regression_contract_pass": current_regression_contract_pass,
        "legacy_regression_profile_valid": legacy_regression_profile_valid,
    }


def _regression_pass_attested(row: dict[str, Any]) -> bool:
    return row.get("passed") is True or str(row.get("fixture_result") or "").upper() == "PASS"


def _regression_expectations_match(row: dict[str, Any]) -> bool:
    compared = False
    for expected_field, actual_field in (
        ("expected_verdict", "actual_verdict"),
        ("expected", "actual"),
        ("expected_candidate_eligible", "actual_candidate_eligible"),
        ("expected_reason", "actual_reason"),
        ("expected_fail_reason", "actual_fail_reason"),
    ):
        if expected_field not in row and actual_field not in row:
            continue
        compared = True
        if expected_field not in row or actual_field not in row:
            return False
        if row[expected_field] != row[actual_field]:
            return False
    return compared


def _current_regression_row_passes(row: dict[str, Any]) -> bool:
    return (
        row.get("passed") is True
        and "expected_verdict" in row
        and "actual_verdict" in row
        and "expected_fail_reason" in row
        and "actual_fail_reason" in row
        and _regression_expectations_match(row)
    )


def _audit_negative_candidate_witness(
    row: ArtifactRow,
    *,
    screening: dict[str, Any],
    final_candidate_ids: set[str],
    facts: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    disposition_aliases: dict[str, str],
    failures: list[str],
    external_quote_verification: list[str],
) -> None:
    verdict = _semantic_verdict_value(row.row) or ""
    valid_verdict = verdict in {
        "PASS",
        "PASSED",
        "PASS_NONFINAL",
        "REJECTED",
        "REJECT",
        "EXCLUDED",
        "REJECTED_OR_AUDIT",
        "REJECT_OR_CONTEXT_ONLY",
        "FAIL",
        "FAILED",
    } or verdict.startswith(("PASS_AS_REJECTION", "PASS_AS_EXCLUDE"))
    valid_verdict = valid_verdict or _is_negative_semantic_verdict(verdict)
    if not valid_verdict:
        failures.append(f"{row.canonical_name}:{row.origin_key}:negative_verdict")
    decision = str(row.row.get("screening_decision") or screening.get("screening_decision") or "").upper()
    candidate_id = _string(row.row.get("candidate_id") or screening.get("candidate_id"))
    witness_outcome = str(row.row.get("witness_outcome") or "").upper()
    explicit_final_ineligible = any(
        value is False
        for value in (
            row.row.get("candidate_final_eligible"),
            row.row.get("final_eligible_semantic"),
            row.row.get("final_eligible"),
            screening.get("semantic_final_eligible"),
        )
    )
    proposed_entailment = row.row.get("proposed_final_entailment")
    explicit_semantic_rejection = _is_negative_semantic_verdict(verdict) and any(
        row.row.get(field) is False
        for field in (
            "economic_mechanism_supported_by_quote",
            "material_fact_class_allowed_by_quote_role",
            "quote_role_allowed_by_catalyst_type",
        )
    )
    entailment_rejected = proposed_entailment is False or (
        proposed_entailment is None
        and (
            explicit_semantic_rejection
            or str(screening.get("final_quality_tier") or "").upper() in {"AUDIT_ONLY", "CONTEXT_ONLY", "REJECTED"}
        )
    )
    outcome_rejected = any(
        token in witness_outcome for token in ("REJECT", "CONTEXT", "EXCLUDE", "NOT_FINAL", "AUDIT")
    ) or (not witness_outcome and _is_negative_semantic_verdict(verdict))
    explicit_post_screening_rejection = all(
        (
            explicit_final_ineligible,
            entailment_rejected,
            outcome_rejected,
            bool(
                _string_list(row.row.get("fail_reasons"))
                or _string(row.row.get("rejection_reason_if_not_final_eligible") or row.row.get("rejection_reason"))
            ),
            candidate_id is not None,
            candidate_id not in final_candidate_ids,
        )
    )
    if decision in _RANKABLE_SCREENING_DECISIONS and not explicit_post_screening_rejection:
        failures.append(f"{row.canonical_name}:{row.origin_key}:negative_decision_mismatch")
    reason = _string(
        row.row.get("negative_reason")
        or row.row.get("rejection_reason")
        or screening.get("no_fact_rejection_reason")
        or screening.get("decision_reason_specific")
        or screening.get("why_not_final_if_rejected")
    )
    if reason is None and not _string_list(row.row.get("fail_reasons")):
        failures.append(f"{row.canonical_name}:{row.origin_key}:negative_reason")
    fact_id = _semantic_fact_id(row.row)
    if fact_id is None:
        screening_fact_ids = _string_list(screening.get("source_fact_ids"))
        if len(screening_fact_ids) == 1:
            fact_id = screening_fact_ids[0]
    fact = facts.get(fact_id or "")
    quote = _string(_semantic_quote(row.row) or (fact or {}).get("exact_quote"))
    if quote is None:
        failures.append(f"{row.canonical_name}:{row.origin_key}:negative_source_quote")
        return
    source_candidates = _field_string_values(
        row.row,
        "source_id",
        "source_row_id",
        "row_id",
    )
    if fact is not None:
        source_candidates.update(_field_string_values(fact, "source_id", "source_row_id"))
    source_candidates.update(disposition_aliases.get(candidate, "") for candidate in tuple(source_candidates))
    source = next(
        (sources[candidate] for candidate in source_candidates if candidate in sources),
        None,
    )
    if source is None:
        failures.append(f"{row.canonical_name}:{row.origin_key}:negative_source")
        return
    source_text = _source_text(source)
    if quote not in source_text:
        if _quote_can_be_verified_from_hashed_source(
            source,
            fact=fact,
            allow_source_only=True,
        ):
            external_quote_verification.append(f"{row.canonical_name}:{row.origin_key}:negative_quote_external")
        else:
            failures.append(f"{row.canonical_name}:{row.origin_key}:negative_quote_not_in_source")


def _audit_semantic_quote_linkage(
    row: ArtifactRow,
    *,
    semantic_row: dict[str, Any],
    facts: dict[str, dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    disposition_aliases: dict[str, str],
    failures: list[str],
    external_quote_verification: list[str],
) -> None:
    quote = _string(semantic_row.get("primary_quote"))
    fact_id = _string(semantic_row.get("primary_fact_id"))
    if quote is None or fact_id is None:
        if fact_id is None:
            failures.append(f"{row.canonical_name}:{row.origin_key}:primary_fact_id")
        return
    fact = facts.get(fact_id)
    if fact is None:
        failures.append(f"{row.canonical_name}:{row.origin_key}:missing_fact")
        return
    fact_quote = _string(fact.get("exact_quote"))
    if fact_quote is None or quote != fact_quote:
        failures.append(f"{row.canonical_name}:{row.origin_key}:fact_quote_mismatch")
    source_candidates = _field_string_values(fact, "source_id", "source_row_id")
    source_candidates.update(disposition_aliases.get(candidate, "") for candidate in tuple(source_candidates))
    source = next(
        (sources[candidate] for candidate in source_candidates if candidate in sources),
        None,
    )
    if source is None:
        failures.append(f"{row.canonical_name}:{row.origin_key}:missing_source")
        return
    source_text = _source_text(source)
    if quote not in source_text:
        if _quote_can_be_verified_from_hashed_source(source, fact=fact):
            external_quote_verification.append(f"{row.canonical_name}:{row.origin_key}:quote_external")
        else:
            failures.append(f"{row.canonical_name}:{row.origin_key}:quote_not_in_source")


def _source_text(source: dict[str, Any]) -> str:
    return "\n".join(
        str(source.get(field) or "")
        for field in (
            "title",
            "headline",
            "body",
            "body_text",
            "article_body",
            "content",
            "raw_text",
        )
    )


def _quote_can_be_verified_from_hashed_source(
    source: dict[str, Any],
    *,
    fact: dict[str, Any] | None,
    allow_source_only: bool = False,
) -> bool:
    if fact is None:
        return (
            allow_source_only
            and _is_news_source_row(source)
            and _is_sha256(source.get("content_sha256"))
            and _is_sha256(source.get("raw_row_sha256"))
            and _is_sha256(source.get("input_sha256"))
            and str(source.get("input_file") or "").lower().endswith(".csv")
        )
    if fact.get("quote_found_in_source_row") is not True:
        return False
    if source.get("body_missing") is False and _is_sha256(source.get("content_sha256")):
        return True
    return _is_sha256(source.get("raw_row_sha256"))


def _semantic_verdict_value(row: dict[str, Any]) -> str | None:
    if (
        row.get("semantic_pass") is True
        or row.get("semantic_passed") is True
        or row.get("final_semantic_pass") is True
    ):
        return "PASS"
    if (
        row.get("semantic_pass") is False
        or row.get("semantic_passed") is False
        or row.get("final_semantic_pass") is False
    ):
        return "FAIL"
    value = _first(
        row,
        "semantic_verdict",
        "screening_semantic_verdict",
        "semantic_audit_status",
        "witness_status",
        "witness_decision",
        "semantic_entailment",
        "semantic_decision",
        "audit_status",
        "audit_result",
        "status",
    )
    if value is None:
        return None
    normalized = value.upper()
    if normalized in {
        "SUPPORTED",
        "VALID",
        "INCLUDE",
        "WATCH_SECONDARY",
        "PASS_TO_RANKING",
    }:
        return "PASS"
    return normalized


def _is_negative_semantic_verdict(verdict: str | None) -> bool:
    if verdict is None:
        return False
    return any(
        token in verdict
        for token in (
            "FAIL",
            "REJECT",
            "AUDIT_ONLY",
            "NOT_FINAL_AUDIT",
            "NONSCORING",
            "EXCLUDE",
        )
    )


def _semantic_fact_id(row: dict[str, Any]) -> str | None:
    direct = _first(
        row,
        "primary_fact_id",
        "source_fact_id",
        "fact_id",
    )
    if direct is not None:
        return direct
    for field in ("primary_fact_ids", "source_fact_ids"):
        values = _string_list(row.get(field))
        if len(values) == 1:
            return values[0]
    return None


def _semantic_quote(row: dict[str, Any]) -> str | None:
    return _first(
        row,
        "primary_quote",
        "source_quote",
        "exact_quote",
    )


def _temporal_audit(
    rows: list[ArtifactRow],
    *,
    require_current_contract: bool = False,
    verified_source_timestamps: dict[str, str] | None = None,
) -> dict[str, Any]:
    by_name = _rows_by_name(rows)
    failures: list[str] = []
    ordered_access_rows = sorted(
        (
            (stream_order, _access_sequence(row), row)
            for stream_order, block_name in enumerate(("access_log.jsonl", "postseal_access_log.jsonl"))
            for row in by_name.get(block_name, [])
        ),
        key=lambda item: (item[0], item[1]),
    )
    access_rows = [row for _, _, row in ordered_access_rows]
    seal_positions = [
        (stream_order, sequence)
        for stream_order, sequence, row in ordered_access_rows
        if _is_verified_seal_row(row.row)
    ]
    outcome_accesses = [
        ((stream_order, sequence), row)
        for stream_order, sequence, row in ordered_access_rows
        if _is_actual_outcome_access(row.row)
    ]
    access_attestation_conflict_count = sum(1 for row in access_rows if _outcome_access_attestation_conflict(row.row))
    if access_attestation_conflict_count:
        failures.append("access_log:outcome_access_attestation_conflict")
    first_outcome = min(outcome_accesses, key=lambda item: item[0]) if outcome_accesses else None
    embedded_seal_verified = _embedded_legacy_seal_verified(by_name)
    first_outcome_attests_prior_seal = bool(
        first_outcome is not None
        and (
            _seal_attested_before_access(first_outcome[1].row)
            or (
                embedded_seal_verified
                and _access_declares_after_seal(first_outcome[1].row)
            )
        )
    )
    if access_rows and (not outcome_accesses or (not seal_positions and not first_outcome_attests_prior_seal)):
        failures.append("access_log:seal_or_outcome_sequence_missing")
    elif (
        first_outcome is not None
        and not first_outcome_attests_prior_seal
        and not any(position < first_outcome[0] for position in seal_positions)
    ):
        failures.append("access_log:outcome_not_strictly_after_seal")

    seal_rows = by_name.get("blind_seal_receipt.json", [])
    if not seal_rows:
        failures.append("blind_seal_receipt:missing")
    else:
        seal = seal_rows[0].row
        counter_fields = sorted(
            field for field in seal if field.startswith("preseal_outcome_") and field.endswith("_count")
        )
        for field in counter_fields:
            if _int(seal.get(field), default=-1) != 0:
                failures.append(f"blind_seal_receipt:{field}")
        missing_counter_fields = sorted(_CURRENT_PRESEAL_COUNTERS - set(counter_fields))
        if require_current_contract and missing_counter_fields:
            failures.append("blind_seal_receipt:required_preseal_counters_missing")
        if (require_current_contract or "receipt_written_before_any_outcome_access" in seal) and seal.get(
            "receipt_written_before_any_outcome_access"
        ) is not True:
            failures.append("blind_seal_receipt:ordering_attestation")

    cutoff = _bundle_cutoff(by_name)
    context_reference = cutoff or _bundle_trade_date(by_name)
    if require_current_contract and cutoff is None:
        failures.append("blind_prediction:cutoff_missing_or_invalid")
    time_unverified_source_ids: set[str] = set()
    source_timestamp_overrides = verified_source_timestamps or {}
    for row in by_name.get("source_ledger.jsonl", []):
        if not _is_news_source_row(row.row):
            continue
        usage_phase = str(row.row.get("usage_phase") or "").upper()
        if row.row.get("used_in_blind") is not True and "BLIND" not in usage_phase:
            continue
        source_id = _first(row.row, "source_id", "source_row_id", "row_id")
        timestamp_override = source_timestamp_overrides.get(source_id or "")
        published = _parse_datetime_or_none(
            row.row.get("published_at_kst") or row.row.get("published_at") or timestamp_override
        )
        if published is None or (row.row.get("time_verified") is False and timestamp_override is None):
            if source_id is not None:
                time_unverified_source_ids.add(source_id)
            continue
        if cutoff is not None and published > cutoff:
            failures.append(f"source_ledger:{row.origin_key}:after_cutoff")
    time_unverified_source_count = len(time_unverified_source_ids)
    blind_decision_time_unverified_source_count = _final_decision_time_unverified_source_count(
        by_name,
        time_unverified_source_ids=time_unverified_source_ids,
    )
    if blind_decision_time_unverified_source_count:
        failures.append("blind_prediction:time_unverified_source_used")

    naive_datetime_count = 0
    available_from_violation_count = 0
    company_known_at_violation_count = 0
    for row in by_name.get("brain_delta.jsonl", []):
        raw_available = row.row.get("available_from")
        available = _parse_datetime_or_none(raw_available)
        if available is None:
            available_from_violation_count += 1
            continue
        if not _has_explicit_timezone(raw_available):
            naive_datetime_count += 1
        if str(row.row.get("record_type") or "") == "company_memory_delta":
            raw_known_at = row.row.get("known_at") or (
                row.row.get("payload", {}).get("known_at") if isinstance(row.row.get("payload"), dict) else None
            )
            known_at = _parse_datetime_or_none(raw_known_at)
            if (require_current_contract and known_at is None) or (known_at is not None and known_at < available):
                company_known_at_violation_count += 1
    if naive_datetime_count:
        failures.append("brain_delta:naive_datetime")
    if available_from_violation_count:
        failures.append("brain_delta:available_from_invalid")
    if company_known_at_violation_count:
        failures.append("brain_delta:company_known_at_before_available_from")

    blind_outcome_key_count = 0
    prior_context_verified = _bundle_prior_context_verified(
        by_name,
        cutoff=context_reference,
    )
    screenings_by_id = {
        screening_id: artifact_row.row
        for artifact_row in by_name.get("candidate_screening.jsonl", [])
        for screening_id in [_first(artifact_row.row, "screening_id")]
        if screening_id is not None
    }
    for block_name in (
        "candidate_screening.jsonl",
        "candidate_ranking_audit.jsonl",
        "fact_ledger_blind.jsonl",
        "inference_ledger_blind.jsonl",
    ):
        for row in by_name.get(block_name, []):
            row_prior_context_verified = prior_context_verified or (
                block_name == "candidate_ranking_audit.jsonl"
                and _ranking_row_has_verified_prior_context(
                    row.row,
                    screenings_by_id=screenings_by_id,
                    cutoff=context_reference,
                )
            )
            if _contains_outcome_only_payload(
                row.row,
                cutoff=context_reference,
                prior_context_verified=row_prior_context_verified,
            ):
                blind_outcome_key_count += 1
    if blind_outcome_key_count:
        failures.append("blind_payload:outcome_fields_present")
    return {
        "failure_count": len(failures),
        "failures": failures,
        "blind_outcome_leak_count": blind_outcome_key_count,
        "naive_datetime_count": naive_datetime_count,
        "available_from_violation_count": available_from_violation_count,
        "company_known_at_violation_count": company_known_at_violation_count,
        "time_unverified_source_count": time_unverified_source_count,
        "blind_decision_time_unverified_source_count": (blind_decision_time_unverified_source_count),
        "missing_required_preseal_counter_count": (
            len(missing_counter_fields) if seal_rows else len(_CURRENT_PRESEAL_COUNTERS)
        ),
        "access_attestation_conflict_count": access_attestation_conflict_count,
    }


def _is_verified_seal_row(row: dict[str, Any]) -> bool:
    action = _access_action(row)
    role = _access_resource(row)
    status = " ".join(
        str(row.get(field) or "").upper() for field in ("status", "result", "verification_status")
    ).strip()
    explicit_verified = next(
        (
            row[field]
            for field in ("verified", "seal_verified", "blind_seal_verified")
            if isinstance(row.get(field), bool)
        ),
        None,
    )
    if explicit_verified is not None:
        verified = explicit_verified is True
    elif status:
        verified = any(
            token in status for token in ("PASS", "PASSED", "SUCCESS", "VERIFIED", "RECONSTRUCTED_VERIFIED")
        ) and not any(token in status for token in ("FAIL", "ERROR", "INVALID"))
    else:
        verified = (
            row.get("allowed") is True
            or (
                "VERIFY" in action
                and not any(token in action for token in ("FAIL", "ERROR", "INVALID"))
                and (_is_sha256(row.get("sha256")) or action.strip() == "VERIFY" or "REPARSE_VERIFY" in action)
            )
            or ("VERIFIED" in role and _is_sha256(row.get("blind_seal_receipt_sha256")))
            or (
                "VERIFIED" in action
                and isinstance(row.get("details"), dict)
                and _is_sha256(row["details"].get("seal_receipt_sha256"))
            )
        )
    verifies = (
        "VERIFY" in action
        or "VERIFIED" in action
        or "VERIFY" in role
        or "VERIFIED" in role
        or ("VERIFIED" in status and "BLIND_SEAL" in f"{action} {role}")
    )
    return verifies and "BLIND_SEAL" in f"{action} {role}" and verified


def _access_sequence(row: ArtifactRow) -> int:
    return _int(
        row.row.get("seq"),
        default=_int(row.row.get("sequence"), default=row.row_ordinal),
    )


def _access_action(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(field) or "").upper()
        for field in (
            "action",
            "access_type",
            "access_mode",
            "event",
            "operation",
            "method",
        )
    )


def _access_resource(row: dict[str, Any]) -> str:
    return " ".join(
        str(row.get(field) or "").upper()
        for field in (
            "logical_role",
            "resource",
            "resource_type",
            "artifact",
            "path_or_url",
            "path",
            "target",
        )
    )


def _seal_attested_before_access(row: dict[str, Any]) -> bool:
    for field in (
        "blind_seal_verified_before_access",
        "blind_seal_receipt_verified_before_access",
        "after_verified_blind_seal",
    ):
        if row.get(field) is True:
            return True
    receipt_sha = row.get("blind_seal_receipt_sha256_verified_before_access")
    if _is_sha256(receipt_sha):
        return True
    combined = f"{_access_action(row)} {_access_resource(row)} {row.get('phase') or ''}".upper()
    return "AFTER_VERIFIED_SEAL" in combined or "AFTER_VERIFIED_BLIND_SEAL" in combined


def _access_declares_after_seal(row: dict[str, Any]) -> bool:
    combined = f"{_access_action(row)} {_access_resource(row)} {row.get('phase') or ''}".upper()
    return any(
        token in combined
        for token in (
            "AFTER_SEAL",
            "POSTSEAL",
            "POST_SEAL",
            "AFTER_BLIND_SEAL",
        )
    )


def _embedded_legacy_seal_verified(
    by_name: dict[str, list[ArtifactRow]],
) -> bool:
    """Recognize a legacy receipt only when its independent fields agree."""

    manifests = by_name.get("blind_packet_manifest.json", [])
    receipts = by_name.get("blind_seal_receipt.json", [])
    if len(manifests) != 1 or len(receipts) != 1:
        return False
    manifest = manifests[0].row
    receipt = receipts[0].row
    if manifest.get("sealed_before_outcome") is not True:
        return False
    status = str(
        receipt.get("verification_status")
        or receipt.get("status")
        or ""
    ).upper()
    if not any(token in status for token in ("PASS", "VERIFIED", "SUCCESS")):
        return False
    if any(token in status for token in ("FAIL", "ERROR", "INVALID")):
        return False
    if receipt.get("outcome_bytes_opened") is not False:
        return False
    if receipt.get("preseal_outcome_access_all_zero") is not True:
        return False
    if not _is_sha256(receipt.get("blind_packet_manifest_sha256")):
        return False
    manifest_size = receipt.get("blind_packet_manifest_byte_size")
    if not isinstance(manifest_size, int) or isinstance(manifest_size, bool) or manifest_size <= 0:
        return False
    counters = {
        field: value
        for field, value in receipt.items()
        if field.startswith("preseal_outcome_") and field.endswith("_count")
    }
    if not counters or any(_int(value, default=-1) != 0 for value in counters.values()):
        return False
    manifest_trade_date = _first(manifest, "trade_date", "calendar_date")
    receipt_trade_date = _first(receipt, "trade_date", "calendar_date")
    manifest_cutoff = _first(manifest, "cutoff", "cutoff_at", "cutoff_kst")
    receipt_cutoff = _first(receipt, "cutoff", "cutoff_at", "cutoff_kst")
    return (
        manifest_trade_date is not None
        and manifest_trade_date == receipt_trade_date
        and manifest_cutoff is not None
        and manifest_cutoff == receipt_cutoff
    )


def _final_decision_time_unverified_source_count(
    by_name: dict[str, list[ArtifactRow]],
    *,
    time_unverified_source_ids: set[str],
) -> int:
    if not time_unverified_source_ids:
        return 0
    source_aliases: dict[str, str] = {}
    for source in by_name.get("source_ledger.jsonl", []):
        source_id = _first(source.row, "source_id", "source_row_id", "row_id")
        if source_id is None:
            continue
        for alias in _field_string_values(
            source.row,
            "source_id",
            "source_row_id",
            "row_id",
        ):
            source_aliases[alias] = source_id
    for disposition in by_name.get("row_disposition.jsonl", []):
        row_id = _first(disposition.row, "row_id")
        source_id = _first(disposition.row, "source_row_id", "source_id")
        if row_id is not None and source_id is not None:
            source_aliases[row_id] = source_aliases.get(source_id, source_id)

    fact_sources: dict[str, set[str]] = {}
    for fact in by_name.get("fact_ledger_blind.jsonl", []):
        fact_id = _first(fact.row, "fact_id")
        if fact_id is None:
            continue
        fact_sources[fact_id] = {
            source_aliases.get(source_id, source_id)
            for source_id in _field_string_values(
                fact.row,
                "source_id",
                "source_row_id",
                "provenance_source_ids",
            )
        }
    inference_facts = {
        inference_id: _field_string_values(
            inference.row,
            "source_fact_ids",
            "supporting_fact_ids",
            "fact_ids",
        )
        for inference in by_name.get("inference_ledger_blind.jsonl", [])
        for inference_id in [_first(inference.row, "inference_id")]
        if inference_id is not None
    }
    screenings = {
        screening_id: screening.row
        for screening in by_name.get("candidate_screening.jsonl", [])
        for screening_id in [_first(screening.row, "screening_id")]
        if screening_id is not None
    }
    contaminated_candidates = 0
    for prediction in by_name.get("blind_prediction.json", []):
        final_watchlist = prediction.row.get("final_watchlist")
        if not isinstance(final_watchlist, list):
            continue
        for candidate in final_watchlist:
            if not isinstance(candidate, dict):
                continue
            screening = screenings.get(
                _string(candidate.get("source_screening_id")) or "",
                {},
            )
            fact_ids = _field_string_values(
                candidate,
                "source_fact_ids",
                "fact_ids",
            ) | _field_string_values(
                screening,
                "source_fact_ids",
                "fact_ids",
            )
            inference_ids = _field_string_values(
                candidate,
                "source_inference_ids",
                "inference_ids",
            ) | _field_string_values(
                screening,
                "source_inference_ids",
                "inference_ids",
            )
            mechanism_inference_id = _string(candidate.get("mechanism_inference_id"))
            if mechanism_inference_id is not None:
                inference_ids.add(mechanism_inference_id)
            fact_ids.update(
                fact_id for inference_id in inference_ids for fact_id in inference_facts.get(inference_id, set())
            )
            resolved_sources = {source_id for fact_id in fact_ids for source_id in fact_sources.get(fact_id, set())}
            resolved_sources.update(
                source_aliases.get(source_id, source_id)
                for source_id in _field_string_values(
                    candidate,
                    "provenance_source_ids",
                    "source_ids",
                )
            )
            if resolved_sources & time_unverified_source_ids:
                contaminated_candidates += 1
    return contaminated_candidates


def _is_actual_outcome_access(row: dict[str, Any]) -> bool:
    action = _access_action(row)
    role = _access_resource(row)
    combined = f"{action} {role}"
    status = " ".join(
        str(row.get(field) or "").upper()
        for field in ("status", "result", "verification_status")
    )
    denied = any(token in combined for token in ("WITHOUT_READ", "QUARANTINED", "BLOCKED", "EXPECTED_PATH"))
    action_access = (
        "OUTCOME" in combined
        and not denied
        and any(
            token in combined
            for token in (
                "OPEN",
                "DOWNLOAD",
                "READ",
                "STAT",
                "HASH",
                "SHA",
                "HEADER",
                "ROW_COUNT",
                "PARSE",
                "LABEL",
                "WINNER_CENSUS",
                "SAMPLE_PRINT",
                "GET",
                "FETCH",
            )
        )
    )
    touched = _outcome_access_flag(row)
    verified_postseal_outcome = (
        "OUTCOME" in combined
        and "POSTSEAL" in status
        and "VERIFIED" in status
        and not denied
    )
    return touched is True or action_access or verified_postseal_outcome


def _outcome_access_attestation_conflict(row: dict[str, Any]) -> bool:
    flag = _outcome_access_flag(row)
    if not isinstance(flag, bool):
        return False
    action = _access_action(row)
    role = _access_resource(row)
    combined = f"{action} {role}"
    denied = any(token in combined for token in ("WITHOUT_READ", "QUARANTINED", "BLOCKED", "EXPECTED_PATH"))
    action_access = (
        "OUTCOME" in combined
        and not denied
        and any(
            token in combined
            for token in (
                "OPEN",
                "DOWNLOAD",
                "READ",
                "STAT",
                "HASH",
                "SHA",
                "HEADER",
                "ROW_COUNT",
                "PARSE",
                "LABEL",
                "WINNER_CENSUS",
                "SAMPLE_PRINT",
                "GET",
                "FETCH",
            )
        )
    )
    if denied:
        return flag is True
    return action_access and flag is False


def _outcome_access_flag(row: dict[str, Any]) -> bool | None:
    for field in (
        "outcome_bytes_touched",
        "outcome_byte_touched",
        "outcome_byte_access",
        "outcome_content_access",
        "outcome_content",
        "outcome_access",
    ):
        value = row.get(field)
        if isinstance(value, bool):
            return value
    return None


def _provenance_and_eligibility_audit(
    repaired_rows: list[ArtifactRow],
    repaired_records: list[dict[str, Any]],
    lineage: list[RecordLineageEntry],
) -> tuple[dict[str, Any], dict[str, Any]]:
    by_name = _rows_by_name(repaired_rows)
    source_ledger = [row.row for row in by_name.get("source_ledger.jsonl", [])]
    known_sources = {
        source_id for row in source_ledger for source_id in [_string(row.get("source_id"))] if source_id is not None
    }
    placeholder_sources = {
        source_id
        for row in source_ledger
        for source_id in [_string(row.get("source_id"))]
        if source_id is not None
        and (
            row.get("provenance_placeholder") is True
            or "PLACEHOLDER" in str(row.get("source_kind") or "").upper()
            or "PLACEHOLDER" in str(row.get("source_type") or "").upper()
        )
    }
    news_sources = {
        source_id
        for row in source_ledger
        for source_id in [_string(row.get("source_id"))]
        if source_id is not None and _is_news_source_row(row)
    }
    source_rows_by_id = {
        source_id: row
        for row in source_ledger
        for source_id in [_string(row.get("source_id"))]
        if source_id is not None
    }
    verified_source_aliases = _verified_news_source_aliases(source_ledger)
    time_unverified_sources = {
        source_id
        for row in source_ledger
        for source_id in [_string(row.get("source_id"))]
        if source_id is not None
        and _is_news_source_row(row)
        and (
            (
                source_rows_by_id.get(
                    verified_source_aliases.get(source_id, source_id),
                    row,
                ).get("time_verified")
                is not True
            )
            or _parse_datetime_or_none(
                source_rows_by_id.get(
                    verified_source_aliases.get(source_id, source_id),
                    row,
                ).get("published_at_kst")
                or source_rows_by_id.get(
                    verified_source_aliases.get(source_id, source_id),
                    row,
                ).get("published_at")
            )
            is None
        )
    }
    row_aliases = {
        row_id: source_id
        for row in by_name.get("row_disposition.jsonl", [])
        for row_id in [_first(row.row, "row_id")]
        for source_id in [_first(row.row, "source_row_id", "source_id")]
        if row_id is not None and source_id is not None
    }
    fact_sources: dict[str, set[str]] = {}
    for block_name in (
        "fact_ledger_blind.jsonl",
        "fact_ledger_postmortem.jsonl",
        "postmortem_fact_ledger.jsonl",
    ):
        for row in by_name.get(block_name, []):
            fact_id = _first(row.row, "fact_id")
            if fact_id is None:
                continue
            candidates = _field_string_values(
                row.row,
                "source_id",
                "source_ids",
                "source_row_id",
                "provenance_source_ids",
            )
            candidates.update(row_aliases.get(value, "") for value in tuple(candidates))
            fact_sources.setdefault(fact_id, set()).update(value for value in candidates if value in known_sources)
    inference_facts: dict[str, set[str]] = {}
    for block_name in (
        "inference_ledger_blind.jsonl",
        "inference_ledger_postmortem.jsonl",
        "postmortem_inference_ledger.jsonl",
    ):
        for row in by_name.get(block_name, []):
            inference_id = _first(row.row, "inference_id")
            if inference_id is not None:
                inference_facts.setdefault(inference_id, set()).update(
                    _field_string_values(
                        row.row,
                        "source_fact_ids",
                        "supporting_fact_ids",
                        "fact_ids",
                    )
                )
    closure_rows: dict[str, dict[str, Any]] = {}
    for closure_row in by_name.get("record_provenance_closure_audit.jsonl", []):
        # Legacy bundles use the local BD id while repaired bundles materialize
        # the episode-namespaced value.  Keep both join aliases; the separate
        # artifact/record lineage audit still verifies the actual ID rewrite.
        record_id = _stable_brain_record_key(closure_row.row)
        if record_id is None:
            continue
        for alias in _record_identity_values(closure_row.row) | {record_id}:
            closure_rows.setdefault(alias, closure_row.row)
    eligible_empty = 0
    eligible_placeholder_only = 0
    eligible_placeholder_reference = 0
    eligible_unresolved = 0
    eligible_time_unverified = 0
    ineligible_nonzero_weight = 0
    ineligible_missing_reason = 0
    closure_content_mismatch = 0
    closure_artifact_absent_nontraining = 0
    closure_artifact_absent_eligible = 0
    for record in repaired_records:
        eligible = record.get("training_eligible") is True
        direct_sources = set(_string_list(record.get("provenance_source_ids")))
        declared_fact_ids = set(_string_list(record.get("source_fact_ids")))
        if not declared_fact_ids:
            declared_fact_ids.update(_string_list(record.get("fact_ids")))
        fact_ids = set(declared_fact_ids)
        inference_ids = set(_string_list(record.get("source_inference_ids")))
        if not inference_ids:
            inference_ids.update(_string_list(record.get("inference_ids")))
        fact_ids.update(
            fact_id for inference_id in inference_ids for fact_id in inference_facts.get(inference_id, set())
        )
        resolved_sources = {source_id for fact_id in fact_ids for source_id in fact_sources.get(fact_id, set())}
        # Retrospective event edges may keep outcome fact/inference IDs for
        # label traceability while removing the post-outcome snapshot from
        # the training provenance.  The renderer records that explicit,
        # lineage-checked filter; do not re-introduce the removed source when
        # recomputing the closure from its retrospective fact ledger.
        if record.get("record_type") == "event_ticker_edge":
            source_filter = record.get("provenance_source_filter")
            if (
                isinstance(source_filter, dict)
                and source_filter.get("rule_id") == "event_ticker_edge_cutoff_safe_sources.v1"
                and set(_string_list(source_filter.get("retained_source_ids"))) == direct_sources
            ):
                removed_sources = set(_string_list(source_filter.get("removed_source_ids")))
                if removed_sources.isdisjoint(direct_sources):
                    resolved_sources.difference_update(removed_sources)
        sources = direct_sources | resolved_sources
        closure_expected_sources = set(sources)
        if direct_sources - placeholder_sources and placeholder_sources & sources:
            # The raw postmortem source may be absent and represented only by
            # a provenance placeholder.  Keep that identity in the record's
            # safety counters, but compare the closure artifact to the real
            # source set that it can actually prove.
            closure_expected_sources.difference_update(placeholder_sources)
        if eligible and not sources:
            eligible_empty += 1
        if eligible and sources and sources <= placeholder_sources:
            eligible_placeholder_only += 1
        if eligible and sources & placeholder_sources:
            eligible_placeholder_reference += 1
        if eligible and not sources <= known_sources:
            eligible_unresolved += 1
        if (
            eligible
            and not sources & news_sources
            and not _record_has_verified_context_provenance(
                record,
                sources=sources,
                source_rows_by_id=source_rows_by_id,
            )
        ):
            eligible_unresolved += 1
        if eligible and sources & time_unverified_sources:
            eligible_time_unverified += 1
        if not eligible:
            if "sample_weight" not in record or abs(_float(record.get("sample_weight"))) > 0.000001:
                ineligible_nonzero_weight += 1
            if not _string(record.get("training_exclusion_reason")):
                ineligible_missing_reason += 1
        record_id = _record_id(record)
        closure = closure_rows.get(record_id or "")
        if closure is None:
            closure = closure_rows.get(_stable_brain_record_key(record) or "")
        if closure is None:
            # Older bundles can omit the optional closure-audit artifact
            # entirely.  When the independently recomputed source/fact/
            # inference closure above is clean, an eligible record is still
            # importable legacy material; the missing artifact is a contract
            # warning, not permission to invent a closure row.  Strict
            # current-gold remains false because the artifact is absent.
            if not closure_rows and eligible:
                closure_artifact_absent_eligible += 1
                continue
            # A record that is already explicitly non-training, carries
            # weight zero, and has a concrete exclusion reason remains safe
            # as zero-weight memory/audit context; do not invent a closure
            # row or turn the whole bundle into a false training gap.
            # A missing row in an otherwise present closure artifact remains a
            # hard mismatch below.
            if (
                not closure_rows
                and not eligible
                and _float(record.get("sample_weight")) == 0.0
                and _string(record.get("training_exclusion_reason"))
            ):
                closure_artifact_absent_nontraining += 1
                continue
            closure_content_mismatch += 1
            continue
        closure_status = _string(
            closure.get("closure_status")
            or closure.get("status")
            or closure.get("provenance_status")
        )
        legacy_pass_closure = (
            closure.get("closure_status") == "PASS"
            and "provenance_source_ids" in closure
            and "unresolved_source_ids" in closure
            and not _string_list(closure.get("unresolved_source_ids"))
            and closure.get("training_eligible") is eligible
            and (
                not closure.get("record_type")
                or closure.get("record_type") == record.get("record_type")
            )
        )
        legacy_passed_closure = (
            closure.get("closure_status") == "PASSED"
            and closure.get("fact_ids_resolve") is True
            and closure.get("inference_ids_resolve") is True
            and closure.get("source_ids_resolve") is True
            and (not eligible or closure.get("required_evidence_nonempty") is True)
            and not _string_list(closure.get("failure_reasons"))
            and closure.get("training_eligible") is eligible
            and (
                not closure.get("record_type")
                or closure.get("record_type") == record.get("record_type")
            )
        )
        legacy_pass_nontraining_quarantine = (
            not eligible
            and closure_status
            in {
                "PASS_NONTRAINING_QUARANTINE",
                "NOT_TRAINING_ELIGIBLE_QUARANTINE",
            }
            and (
                closure.get("source_provenance_closed") is True
                or (
                    _int(closure.get("declared_source_count"), default=-1)
                    == _int(closure.get("resolved_source_count"), default=-2)
                    and _int(closure.get("declared_source_count"), default=-1) >= 0
                )
            )
            and not _string_list(closure.get("unresolved_reference_ids"))
            and not _string_list(closure.get("unresolved_source_ids"))
            and closure.get("training_eligible") is False
        )
        legacy_closure_proof = (
            legacy_pass_closure
            or legacy_passed_closure
            or legacy_pass_nontraining_quarantine
        )
        has_resolved_sources = "resolved_provenance_source_ids" in closure
        closure_sources = set(_string_list(closure.get("resolved_provenance_source_ids")))
        allowed_closure_statuses = (
            {"CLOSED"}
            if eligible
            else {
                "CLOSED",
                "CLOSED_NOT_TRAINING",
                "NOT_TRAINING_NO_CLOSURE_REQUIRED",
            }
        )
        if legacy_pass_closure:
            allowed_closure_statuses.add("PASS")
        if legacy_passed_closure:
            allowed_closure_statuses.add("PASSED")
        if legacy_pass_nontraining_quarantine:
            allowed_closure_statuses.add("PASS_NONTRAINING_QUARANTINE")
            allowed_closure_statuses.add("NOT_TRAINING_ELIGIBLE_QUARANTINE")
        closure_mismatch = closure_status not in allowed_closure_statuses
        legacy_no_closure = (
            not eligible
            and (
                closure_status == "NOT_TRAINING_NO_CLOSURE_REQUIRED"
                or (
                    closure_status == "CLOSED_NOT_TRAINING"
                    and closure.get("training_eligible_after_closure") is False
                    and _float(closure.get("sample_weight_after_closure")) == 0.0
                    and not any(
                        field in closure
                        for field in (
                            "source_fact_ids",
                            "resolved_source_fact_ids",
                            "fact_ids",
                            "source_inference_ids",
                            "resolved_source_inference_ids",
                            "inference_ids",
                        )
                    )
                )
            )
        )
        # Some legacy non-training context rows keep a resolvable inference
        # reference but intentionally omit the derived fact list from the
        # closure audit.  They are safe to retain at zero weight only when
        # every inference resolves through the actual inference ledger and
        # the closure explicitly records CLOSED_NOT_TRAINING.  This is not a
        # provenance waiver for eligible records or unresolved references.
        closed_not_training_without_fact_listing = (
            not eligible
            and closure_status == "CLOSED_NOT_TRAINING"
            and closure.get("training_eligible_after_closure") is False
            and _float(closure.get("sample_weight_after_closure")) == 0.0
            and bool(_string(closure.get("downgrade_reason")))
            and bool(inference_ids)
            and all(inference_id in inference_facts for inference_id in inference_ids)
            and "source_fact_ids" not in closure
            and "fact_ids" not in closure
            and "resolved_source_fact_ids" not in closure
        )
        unresolved_reference_record = (
            not fact_ids
            and not inference_ids
            and _string(record.get("unresolved_reference_reason"))
            == "typed_reference_not_present_in_bundle_ledger"
            and bool(
                _string_list(record.get("legacy_unresolved_fact_tokens"))
                or _string_list(record.get("legacy_unresolved_inference_tokens"))
            )
        )
        inference_omitted_without_refs = (
            not inference_ids
            and not any(
                field in closure
                for field in (
                    "source_inference_ids",
                    "resolved_source_inference_ids",
                    "inference_ids",
                )
            )
        )
        direct_source_only_context = (
            not fact_ids
            and not inference_ids
            and bool(sources)
            and closure.get("closure_status") == "CLOSED"
            and set(_string_list(closure.get("resolved_provenance_source_ids")))
            == closure_expected_sources
            # Some legacy context audits omit the explanatory closure_path,
            # but the explicit resolved source set is still present.  The
            # source-set equality is the authoritative proof in this
            # fact/inference-free shape; a missing prose path is not data
            # loss.
        )
        inference_only_closure = (
            not _string_list(record.get("source_fact_ids"))
            and not _string_list(record.get("fact_ids"))
            and bool(inference_ids)
            and bool(sources)
            and closure.get("closure_status") == "CLOSED"
            and set(_string_list(closure.get("resolved_provenance_source_ids")))
            == closure_expected_sources
            and set(_string_list(closure.get("source_inference_ids"))) == inference_ids
            and all(inference_id in inference_facts for inference_id in inference_ids)
            and all(
                fact_id in fact_sources
                for inference_id in inference_ids
                for fact_id in inference_facts[inference_id]
            )
            and not any(
                field in closure
                for field in ("source_fact_ids", "resolved_source_fact_ids", "fact_ids")
            )
        )
        if legacy_pass_closure:
            closure_sources = set(_string_list(closure.get("provenance_source_ids")))
            closure_mismatch |= closure_sources != closure_expected_sources
        elif has_resolved_sources:
            closure_mismatch |= closure_sources != closure_expected_sources
        elif not legacy_no_closure and not legacy_closure_proof:
            # Legacy closure audits encode the same proof as boolean closure
            # flags and empty missing-* lists instead of repeating resolved
            # source/fact/inference IDs.  Validate that proof directly rather
            # than treating the older representation as data loss.
            closure_mismatch |= closure.get("source_ids_closed") is not True
            closure_mismatch |= bool(_string_list(closure.get("missing_source_ids")))
        if "source_fact_ids" in closure:
            closure_fact_ids = set(_string_list(closure.get("source_fact_ids")))
            if not closure_fact_ids:
                closure_fact_ids.update(_string_list(closure.get("resolved_source_fact_ids")))
            if not closure_fact_ids:
                closure_fact_ids.update(_string_list(closure.get("fact_ids")))
            # Non-training legacy case rows may record only their primary
            # fact while the linked inference expands to several supporting
            # facts.  The primary closure is still source-anchored; require
            # either the record's direct fact list or its exact inference-
            # expanded list for eligible rows.  Explicit zero-weight rows may
            # preserve a non-empty subset of that independently resolved set.
            closure_mismatch |= not (
                closure_fact_ids in (declared_fact_ids, fact_ids)
                or (
                    not eligible
                    and bool(closure_fact_ids)
                    and closure_fact_ids <= fact_ids
                )
            )
        elif (
            not legacy_no_closure
            and not legacy_closure_proof
            and not unresolved_reference_record
            and not direct_source_only_context
            and not inference_only_closure
            and not closed_not_training_without_fact_listing
        ):
            closure_mismatch |= closure.get("fact_ids_closed") is not True
            closure_mismatch |= bool(_string_list(closure.get("missing_fact_ids")))
        if "source_inference_ids" in closure:
            closure_inference_ids = set(_string_list(closure.get("source_inference_ids")))
            if not closure_inference_ids:
                closure_inference_ids.update(_string_list(closure.get("resolved_source_inference_ids")))
            if not closure_inference_ids:
                closure_inference_ids.update(_string_list(closure.get("inference_ids")))
            closure_mismatch |= closure_inference_ids != inference_ids
        elif (
            not legacy_no_closure
            and not legacy_closure_proof
            and not unresolved_reference_record
            and not inference_omitted_without_refs
            and not direct_source_only_context
        ):
            closure_mismatch |= closure.get("inference_ids_closed") is not True
            closure_mismatch |= bool(_string_list(closure.get("missing_inference_ids")))
        if "training_eligible_after_closure" in closure:
            closure_mismatch |= closure.get("training_eligible_after_closure") is not eligible
        elif "training_eligible" in closure:
            closure_mismatch |= closure.get("training_eligible") is not eligible
        if "sample_weight_after_closure" in closure:
            closure_mismatch |= (
                abs(_float(closure.get("sample_weight_after_closure")) - _float(record.get("sample_weight")))
                > 0.000001
            )
        if closure_mismatch:
            closure_content_mismatch += 1
    false_to_true = sum(
        1
        for row in lineage
        if row.lineage_kind == "EXPLICIT"
        and row.training_eligible_before is not True
        and row.training_eligible_after is True
    )
    return (
        {
            "eligible_empty_source_count": eligible_empty,
            "eligible_placeholder_only_count": eligible_placeholder_only,
            "eligible_placeholder_reference_count": eligible_placeholder_reference,
            "eligible_unresolved_source_count": eligible_unresolved,
            "eligible_time_unverified_source_count": eligible_time_unverified,
            "closure_content_mismatch_count": closure_content_mismatch,
            "closure_artifact_absent_nontraining_count": closure_artifact_absent_nontraining,
            "closure_artifact_absent_eligible_count": closure_artifact_absent_eligible,
            "known_source_count": len(known_sources),
            "placeholder_source_count": len(placeholder_sources),
        },
        {
            "false_to_true_count": false_to_true,
            "ineligible_nonzero_weight_count": ineligible_nonzero_weight,
            "ineligible_missing_reason_count": ineligible_missing_reason,
        },
    )


def _record_has_verified_context_provenance(
    record: dict[str, Any],
    *,
    sources: set[str],
    source_rows_by_id: dict[str, dict[str, Any]],
) -> bool:
    if not sources:
        return False
    source_phase = str(record.get("source_phase") or "").upper()
    if "POSTMORTEM" in source_phase or "OUTCOME" in source_phase:
        for source_id in sources:
            source = source_rows_by_id.get(source_id)
            if source is None:
                continue
            source_type = str(source.get("source_type") or source.get("source_kind") or "").upper()
            usage_phase = str(source.get("usage_phase") or "").upper()
            if (
                "OUTCOME_SNAPSHOT" in source_type
                and "POSTSEAL_OUTCOME" in usage_phase
                and source.get("available_before_cutoff") is False
                and source.get("time_verified") is True
                and _int(source.get("byte_size"), default=0) > 0
                and _string(source.get("content_sha256"))
            ):
                return True
    for source_id in sources:
        source = source_rows_by_id.get(source_id)
        if source is None:
            continue
        source_role = " ".join(
            str(source.get(field) or "").upper() for field in ("source_type", "source_kind", "logical_role", "role")
        )
        if (
            "RESEARCH_DAILY_BLIND_SNAPSHOT" in source_role
                and str(
                    source.get("usage_phase") or source.get("source_phase") or ""
                ).upper() == "BLIND"
            and _string(
                source.get("sha256")
                or source.get("content_sha256")
                or source.get("source_file_sha256")
                or source.get("raw_row_sha256")
            )
            and _string(
                source.get("path")
                or source.get("input_file")
                or source.get("source_file")
            )
        ):
            # Ranking/negative postmortem records can legitimately be
            # grounded only in the sealed pre-D snapshot.  This is a real,
            # hashed source, not a fabricated generic provenance marker.
            return True
    for source_id in sources:
        source = source_rows_by_id.get(source_id)
        if source is None:
            continue
        source_role = " ".join(
            str(source.get(field) or "").upper() for field in ("source_type", "source_kind", "logical_role", "role")
        )
        if (
            "POSTMORTEM" in source_phase
            and any(
                token in source_role
                for token in ("POSTSEAL_WEB_PAGE", "POSTSEAL_OFFICIAL_DISCLOSURE")
            )
            and str(source.get("cutoff_relation") or "").upper().startswith("PRE_CUTOFF_")
            and "WEB_VERIFIED" in str(source.get("retrieval_status") or "").upper()
            and _string(source.get("url") or source.get("path"))
            and _string(source.get("exact_excerpt") or source.get("title"))
        ):
            # Postmortem candidate-generation and beneficiary records may be
            # grounded in a verified external page or official disclosure
            # that was published before the trade date but was not in the
            # sealed CSV.  Keep this separate from BLIND NEWS_CSV provenance:
            # the source must explicitly declare a pre-cutoff relation and
            # its publication date must not be after the record's trade date.
            published = _parse_datetime_or_none(
                source.get("published_at_kst") or source.get("published_at")
            )
            trade_date = _parse_datetime_or_none(record.get("trade_date"))
            if (
                published is not None
                and trade_date is not None
                and published.date() <= trade_date.date()
            ):
                return True
    if record.get("record_type") != "context_market_state_or_fact_case":
        return False
    for source_id in sources:
        source = source_rows_by_id.get(source_id)
        if source is None:
            continue
        source_role = " ".join(
            str(source.get(field) or "").upper() for field in ("source_type", "source_kind", "logical_role", "role")
        )
        if (
            "PRICE_SNAPSHOT" in source_role
            and source.get("available_before_cutoff") is True
            and source.get("time_verified") is True
            and source.get("used_in_blind") is True
        ):
            return True
        if (
            "RESEARCH_DAILY_BLIND_SNAPSHOT" in source_role
            and str(
                source.get("usage_phase") or source.get("source_phase") or ""
            ).upper() == "BLIND"
            and _string(
                source.get("sha256")
                or source.get("content_sha256")
                or source.get("source_file_sha256")
                or source.get("raw_row_sha256")
            )
            and _string(
                source.get("path")
                or source.get("input_file")
                or source.get("source_file")
            )
        ):
            # Legacy source-ledger rows sometimes omit row_count even though
            # the immutable snapshot path and digest are present.  The
            # digest/path/BLIND contract is sufficient to prove a concrete
            # pre-cutoff context source; do not reject the brain record merely
            # because this optional census field was not emitted.
            return True
        if (
            "NEWS_CSV" in source_role
            and source.get("available_before_cutoff") is True
            and source.get("time_verified") is True
            and _string(source.get("content_sha256") or source.get("sha256"))
            and (
                bool(_string_list(source.get("input_row_ids")))
                or _string(source.get("path") or source.get("input_file"))
            )
        ):
            # Some legacy bundles identify the sealed D-day news CSV as one
            # hashed file source (rather than repeating the snapshot path).
            # Its cutoff/time/hash plus row manifest prove a real BLIND
            # context source; do not treat the descriptor as unresolved.
            return True
    return False


def _artifact_occurrence_record_id_namespacing_only(
    name: str,
    *,
    source_artifact_rows: list[ArtifactRow] | None = None,
    repaired_artifact_rows: list[ArtifactRow] | None = None,
) -> bool:
    """Allow deterministic episode namespacing on artifact ``record_id``.

    The repair renderer namespaces brain IDs and the case/audit artifacts that
    point at them.  Some legacy case blocks carry the pointer as a scalar
    ``record_id`` rather than a ``brain_record_id`` field.  The artifact is
    still lossless when row count, keys, and every non-ID value are unchanged
    and each old ID maps to its namespaced counterpart.
    """

    source_rows = [
        row.row
        for row in (source_artifact_rows or [])
        if row.canonical_name == name
    ]
    repaired_rows = [
        row.row
        for row in (repaired_artifact_rows or [])
        if row.canonical_name == name
    ]
    if not source_rows or len(source_rows) != len(repaired_rows):
        return False
    changed = False

    def values_match(source_value: Any, repaired_value: Any, *, field: str) -> bool:
        nonlocal changed
        if isinstance(source_value, dict) and isinstance(repaired_value, dict):
            if not set(source_value) <= set(repaired_value):
                return False
            for key in set(repaired_value) - set(source_value):
                if not _is_allowed_added_field(
                    source_value,
                    repaired_value,
                    (key,),
                    repaired_value[key],
                    artifact_name=name,
                ):
                    return False
            return all(
                values_match(source_value[key], repaired_value[key], field=key)
                for key in source_value
            )
        if isinstance(source_value, list) and isinstance(repaired_value, list):
            if len(source_value) != len(repaired_value):
                return False
            return all(
                values_match(left, right, field=field)
                for left, right in zip(source_value, repaired_value, strict=True)
            )
        if source_value == repaired_value:
            return True
        is_identity = (
            field in {"record_id", "brain_delta_id"}
            or field.endswith("_record_id")
            or field.endswith("_record_ids")
        )
        if is_identity and _is_namespaced_value(source_value, repaired_value):
            changed = True
            return True
        return False

    for source_row, repaired_row in zip(source_rows, repaired_rows, strict=True):
        if not values_match(source_row, repaired_row, field="<root>"):
            return False
    return changed


def _artifact_occurrence_source_alias_only(
    name: str,
    *,
    source_path: Path,
    repaired_path: Path,
    source_artifact_rows: list[ArtifactRow] | None = None,
    repaired_artifact_rows: list[ArtifactRow] | None = None,
) -> bool:
    """Accept a source-reference spelling change only when it is lossless.

    A few legacy bundles use ``SRC-000123`` in facts while their source ledger
    uses ``SRC-NEWS-000123``.  Compare parsed rows rather than raw hashes and
    require every changed value to be the same numeric-suffix source alias.
    This does not permit added rows, deleted fields, or arbitrary ID changes.
    """
    source_artifact_rows = source_artifact_rows or artifact_rows(source_path)
    repaired_artifact_rows = repaired_artifact_rows or artifact_rows(repaired_path)
    source_rows = [row.row for row in source_artifact_rows if row.canonical_name == name]
    repaired_rows = [row.row for row in repaired_artifact_rows if row.canonical_name == name]
    if not source_rows or len(source_rows) != len(repaired_rows):
        return False
    source_ledger_ids: set[str] = set()
    for row in source_artifact_rows:
        if row.canonical_name == "source_ledger.jsonl":
            source_id = _string(row.row.get("source_id"))
            if source_id is not None:
                source_ledger_ids.add(source_id)
        elif row.canonical_name == "material_review.jsonl":
            review_id = _string(row.row.get("material_review_id"))
            if review_id is not None:
                source_ledger_ids.add(review_id)
    if not source_ledger_ids:
        return False

    def equal(old: Any, new: Any) -> bool:
        if isinstance(old, dict):
            return isinstance(new, dict) and set(old) == set(new) and all(
                equal(old[key], new[key]) for key in old
            )
        if isinstance(old, list):
            return isinstance(new, list) and len(old) == len(new) and all(
                equal(left, right) for left, right in zip(old, new, strict=True)
            )
        if old == new:
            return True
        return _reference_identifier_alias_equivalent(old, new, source_ledger_ids)

    return all(equal(old, new) for old, new in zip(source_rows, repaired_rows, strict=True))


def _importer_audit(inspection: dict[str, Any]) -> dict[str, Any]:
    return {
        "validation_passed": inspection.get("validation_passed") is True,
        "import_loss_audit_passed": inspection.get("import_loss_audit_passed") is True,
        "missing_normalized_record_count": _int(inspection.get("missing_normalized_record_count"), default=-1),
        "extra_normalized_record_count": _int(inspection.get("extra_normalized_record_count"), default=-1),
        "missing_source_reference_count": _int(inspection.get("missing_source_reference_count"), default=-1),
        "missing_payload_reference_count": _int(inspection.get("missing_payload_reference_count"), default=-1),
        "invalid_typed_payload_record_count": _int(inspection.get("invalid_typed_payload_record_count"), default=-1),
        "final_semantic_audit_fail_count": _int(inspection.get("final_semantic_audit_fail_count"), default=-1),
        "raw_record_count": _int(inspection.get("raw_record_count"), default=-1),
        "normalized_record_count": _int(inspection.get("normalized_record_count"), default=-1),
        "raw_normalized_record_count_matches": (inspection.get("raw_normalized_record_count_matches") is True),
        "training_eligible_count_matches_raw": (inspection.get("training_eligible_count_matches_raw") is True),
        "quarantined_record_count": _int(inspection.get("quarantined_record_count"), default=-1),
    }


def _record_type_token_reconciliation(
    census: SourceCensus,
    rows: list[ArtifactRow],
) -> dict[str, int]:
    raw_bytes = census.source_path.read_bytes()
    token_matches = list(_RAW_RECORD_TYPE_TOKEN_BYTES.finditer(raw_bytes))
    claimed_spans = _merge_byte_spans([(row.raw_row_byte_start, row.raw_row_byte_end) for row in rows])
    # A declared NSLAB marker may intentionally contain documentation or a
    # CSV/Markdown artifact with JSON examples.  Those examples are opaque
    # source material, not unclaimed brain records.  Unknown headings/fences
    # remain subject to the lexical lower-bound check below.
    declared_opaque_spans = _merge_byte_spans(
        [
            (occurrence.byte_start, occurrence.byte_end)
            for occurrence in census.artifact_occurrences
            if occurrence.wrapper_kind == "NSLAB_MARKER"
            and occurrence.declared_format not in {"json", "jsonl"}
        ]
    )
    unclaimed_spans = _merge_byte_spans(
        [(occurrence.byte_start, occurrence.byte_end) for occurrence in census.unclaimed_machine_payloads]
    )
    claimed_starts = [start for start, _ in claimed_spans]
    declared_opaque_starts = [start for start, _ in declared_opaque_spans]
    unclaimed_starts = [start for start, _ in unclaimed_spans]
    claimed_token_count = 0
    unreconciled_token_count = 0
    for match in token_matches:
        token_start, token_end = match.span()
        if _byte_span_is_covered(
            token_start,
            token_end,
            spans=claimed_spans,
            starts=claimed_starts,
        ):
            claimed_token_count += 1
            continue
        if _byte_span_is_covered(
            token_start,
            token_end,
            spans=declared_opaque_spans,
            starts=declared_opaque_starts,
        ):
            continue
        if _byte_span_is_covered(
            token_start,
            token_end,
            spans=unclaimed_spans,
            starts=unclaimed_starts,
        ) or _raw_token_line_is_machine_like(raw_bytes, token_start):
            unreconciled_token_count += 1
    return {
        "raw_token_count": len(token_matches),
        "claimed_token_count": claimed_token_count,
        "unreconciled_token_count": unreconciled_token_count,
    }


def _merge_byte_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[tuple[int, int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            previous_start, previous_end = merged[-1]
            merged[-1] = (previous_start, max(previous_end, end))
        else:
            merged.append((start, end))
    return merged


def _byte_span_is_covered(
    token_start: int,
    token_end: int,
    *,
    spans: list[tuple[int, int]],
    starts: list[int],
) -> bool:
    index = bisect_right(starts, token_start) - 1
    return index >= 0 and token_end <= spans[index][1]


def _raw_token_line_is_machine_like(raw_bytes: bytes, token_start: int) -> bool:
    line_start = raw_bytes.rfind(b"\n", 0, token_start) + 1
    line_end = raw_bytes.find(b"\n", token_start)
    if line_end < 0:
        line_end = len(raw_bytes)
    stripped = raw_bytes[line_start:line_end].lstrip()
    return stripped.startswith((b"{", b"[", b'"record_type"', b"'record_type'"))


def _artifact_parse_issue_count(census: SourceCensus) -> int:
    return sum(
        occurrence.parse_status in {"PARSE_ERROR", "UNDECLARED_MACHINE"}
        for occurrence in census.artifact_occurrences
        if not occurrence.overlapping_alias
    )


def _quality_blockers(**audits: dict[str, Any]) -> list[str]:
    blockers: list[str] = []
    raw = audits["raw_census"]
    for field in (
        "replacement_character_count",
        "unclaimed_machine_payload_count",
        "conflicting_duplicate_block_count",
        "duplicate_block_name_count",
        "source_unreconciled_record_type_token_count",
        "source_artifact_parse_issue_count",
        "repaired_replacement_character_count",
        "repaired_unclaimed_machine_payload_count",
        "repaired_conflicting_duplicate_block_count",
        "repaired_duplicate_block_name_count",
        "repaired_unreconciled_record_type_token_count",
        "repaired_artifact_parse_issue_count",
    ):
        if (
            field
            in {
                "replacement_character_count",
                "repaired_replacement_character_count",
            }
            and raw.get("replacement_character_preserved") is True
        ):
            continue
        if raw.get(field) != 0:
            blockers.append(f"RAW_CENSUS:{field}={raw.get(field)}")
    if raw.get("strict_utf8_ok") is not True or raw.get("repaired_strict_utf8_ok") is not True:
        blockers.append("RAW_CENSUS:strict_utf8_failed")
    critical_counts = {
        "lineage": (
            "unaccounted_original_record_count",
            "orphan_repaired_record_count",
            "cross_record_ref_missing_count",
            "illegal_transform_count",
            "artifact_missing_source_row_count",
            "artifact_orphan_repaired_row_count",
            "artifact_illegal_transform_count",
            "derived_case_link_failure_count",
            "artifact_occurrence_missing_count",
            "artifact_occurrence_changed_count",
            "artifact_occurrence_orphan_count",
            "false_to_true_count",
        ),
        "population": (
            "population_underfill_count",
            "population_extra_count",
            "duplicate_logical_key_count",
            "liquidity_policy_underspecified_count",
        ),
        "provenance": (
            "eligible_empty_source_count",
            "eligible_placeholder_only_count",
            "eligible_unresolved_source_count",
            "eligible_time_unverified_source_count",
            "closure_content_mismatch_count",
            "timestamp_repair_failure_count",
        ),
        "eligibility": (
            "false_to_true_count",
            "ineligible_nonzero_weight_count",
            "semantic_invalid_training_eligible_count",
        ),
    }
    for audit_name, fields in critical_counts.items():
        audit = audits[audit_name]
        for field in fields:
            if (
                audit_name == "lineage"
                and field == "derived_case_link_failure_count"
                and audit.get(
                    "derived_case_link_effective_failure_count",
                    audit.get(field, 0),
                )
                == 0
            ):
                continue
            if (
                audit_name == "population"
                and audit.get("legacy_contract_population_quarantine") is True
                and field
                in {
                    "population_underfill_count",
                    "population_extra_count",
                    "liquidity_policy_underspecified_count",
                }
            ):
                continue
            value = audit.get(field)
            if isinstance(value, int) and value > 0:
                blockers.append(f"{audit_name.upper()}:{field}={value}")
    importer = audits["importer"]
    if importer.get("validation_passed") is not True:
        blockers.append("IMPORTER:validation_failed")
    for field in (
        "import_loss_audit_passed",
        "raw_normalized_record_count_matches",
        "training_eligible_count_matches_raw",
    ):
        if importer.get(field) is not True:
            blockers.append(f"IMPORTER:{field}=false")
    if importer.get("quarantined_record_count") != 0:
        blockers.append(f"IMPORTER:quarantined_record_count={importer.get('quarantined_record_count')}")
    if audits["deterministic"].get("matches") is not True:
        blockers.append("DETERMINISM:not_verified")
    if audits["ephemeral_store"].get("passed") is not True:
        blockers.append("EPHEMERAL_STORE:not_verified")
    if audits["ephemeral_store"].get("real_store_unchanged") is not True:
        blockers.append("REAL_STORE_UNCHANGED:pending_or_failed")
    return sorted(set(blockers))


def _rows_by_name(rows: list[ArtifactRow]) -> dict[str, list[ArtifactRow]]:
    grouped: dict[str, list[ArtifactRow]] = defaultdict(list)
    seen_occurrence: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (row.canonical_name, row.raw_payload_sha256)
        prior_occurrence = seen_occurrence.get(key)
        if prior_occurrence is not None and prior_occurrence != row.occurrence_id:
            continue
        seen_occurrence.setdefault(key, row.occurrence_id)
        grouped[row.canonical_name].append(row)
    return grouped


def _keys(rows: Iterable[ArtifactRow], *fields: str) -> set[str]:
    return {value for row in rows for value in [_first(row.row, *fields)] if value is not None}


def _field_string_values(row: dict[str, Any], *fields: str) -> set[str]:
    values: set[str] = set()
    for field in fields:
        value = row.get(field)
        if isinstance(value, str) and value:
            values.add(value)
        elif isinstance(value, list):
            values.update(str(item) for item in value if isinstance(item, str) and item)
    return values


def _alias_graph(
    rows: Iterable[ArtifactRow],
    *,
    fields: tuple[str, ...],
    verified_links: dict[str, str] | None = None,
) -> dict[str, str]:
    parent: dict[str, str] = {}

    def find(value: str) -> str:
        parent.setdefault(value, value)
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        if left_root < right_root:
            parent[right_root] = left_root
        else:
            parent[left_root] = right_root

    for artifact_row in rows:
        values = sorted(_field_string_values(artifact_row.row, *fields))
        if not values:
            continue
        find(values[0])
        for value in values[1:]:
            union(values[0], value)
    for alias, canonical in sorted((verified_links or {}).items()):
        union(alias, canonical)
    return {value: find(value) for value in parent}


def _verified_news_source_aliases(
    rows: Iterable[dict[str, Any]],
) -> dict[str, str]:
    """Resolve explicit news-row aliases only when row identity is unique."""

    canonical_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    alias_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        source_id = _string(row.get("source_id"))
        source_type = str(row.get("source_type") or row.get("source_kind") or "").upper()
        if source_id is None:
            continue
        if source_type == "NEWS_CSV_ROW":
            canonical_rows[source_id].append(row)
        elif source_type == "NEWS_CSV_ROW_ALIAS":
            alias_rows[source_id].append(row)

    verified: dict[str, str] = {}
    for alias_id, declarations in alias_rows.items():
        if len(declarations) != 1:
            continue
        declaration = declarations[0]
        canonical_id = _string(declaration.get("canonical_source_id"))
        candidates = canonical_rows.get(canonical_id or "", [])
        if canonical_id is None or canonical_id == alias_id or len(candidates) != 1:
            continue
        canonical = candidates[0]
        alias_hash = _string(declaration.get("raw_row_sha256"))
        canonical_hash = _string(canonical.get("raw_row_sha256"))
        alias_index = _source_row_index(declaration.get("row_index"))
        canonical_index = _source_row_index(canonical.get("row_index"))
        if (
            alias_hash is None
            or canonical_hash is None
            or re.fullmatch(r"[0-9a-fA-F]{64}", alias_hash) is None
            or alias_hash.lower() != canonical_hash.lower()
            or alias_index is None
            or alias_index != canonical_index
        ):
            continue
        verified[alias_id] = canonical_id
    return verified


def _source_row_index(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _logical_alias_keys(
    rows: Iterable[ArtifactRow],
    *,
    aliases: dict[str, str],
    fields: tuple[str, ...],
) -> set[str]:
    return {
        aliases.get(value, value) for artifact_row in rows for value in _field_string_values(artifact_row.row, *fields)
    }


def _is_aggregate_news_source(row: dict[str, Any]) -> bool:
    source_type = str(row.get("source_type") or row.get("source_kind") or "").upper()
    logical_role = str(row.get("logical_role") or "").upper()
    has_row_identity = bool(
        _field_string_values(row, "source_row_id", "row_id")
        or (
            isinstance(row.get("row_id"), int)
            and not isinstance(row.get("row_id"), bool)
        )
        or isinstance(row.get("row_index"), int)
        or _source_row_index(row.get("source_row_index")) is not None
    )
    if has_row_identity:
        return False
    return source_type in {
        "NEWS_CSV",
        "NEWS_CSV_FILE",
        "NEWS_CSV_INPUT",
        "NEWS_CSV_RAW",
        "NEWS_CSV_RAW_BYTES",
        "NEWS_INPUT_FILE",
    } or logical_role in {
        "INPUT_NEWS_CSV",
        "NEWS_CSV_FILE",
    }


def _is_news_source_row(row: dict[str, Any]) -> bool:
    if _is_aggregate_news_source(row):
        return False
    source_type = str(row.get("source_type") or row.get("source_kind") or "").upper()
    logical_role = str(row.get("logical_role") or "").upper()
    source_id = str(row.get("source_id") or "").upper()
    if source_type:
        return "NEWS" in source_type or "CSV_ROW" in source_type
    if logical_role:
        return logical_role in {
            "NEWS_ROW",
            "INPUT_NEWS_ROW",
            "NEWS_INPUT",
            "INPUT_NEWS_CSV",
            "NEWS_INPUT_FILE",
        }
    has_row_identity = bool(
        _field_string_values(row, "source_row_id", "row_id")
        or (
            isinstance(row.get("row_id"), int)
            and not isinstance(row.get("row_id"), bool)
        )
        or isinstance(row.get("row_index"), int)
        or _source_row_index(row.get("source_row_index")) is not None
    )
    has_article_payload = any(
        _string(row.get(field)) is not None
        for field in ("title", "headline", "body", "body_text", "published_at", "published_at_kst")
    )
    if has_row_identity and has_article_payload:
        return True
    return (
        source_id.startswith("SRC-NEWS-ROW-")
        or source_id.startswith("SRC-NEWS-")
        or source_id.startswith("SRC-000")
    )


def _row_requires_material_review(row: dict[str, Any]) -> bool:
    explicit = row.get("material_review_queue_member")
    if isinstance(explicit, bool):
        return explicit
    return _string(row.get("disposition") or row.get("primary_disposition")) in _MATERIAL_DISPOSITIONS


def _first(row: dict[str, Any], *fields: str) -> str | None:
    for field in fields:
        value = _string(row.get(field))
        if value is not None:
            return value
    return None


def _add_exact_rule(
    rules: dict[str, dict[str, Any]],
    name: str,
    expected: set[str],
    actual: set[str],
) -> None:
    rules[name] = {
        "mode": "EXACT",
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing_keys": sorted(expected - actual),
        "extra_keys": sorted(actual - expected),
    }


def _add_subset_rule(
    rules: dict[str, dict[str, Any]],
    name: str,
    expected: set[str],
    actual: set[str],
) -> None:
    rules[name] = {
        "mode": "EXPECTED_SUBSET",
        "expected_count": len(expected),
        "actual_count": len(actual),
        "missing_keys": sorted(expected - actual),
        "extra_keys": sorted(actual - expected),
    }


def _screening_population_key(row: dict[str, Any]) -> str | None:
    ticker = _first(row, "ticker", "code")
    if ticker is not None:
        return f"TICKER:{ticker}"
    screening_id = _first(row, "screening_id")
    return f"SCREENING:{screening_id}" if screening_id is not None else None


def _screening_identity(row: dict[str, Any]) -> str | None:
    """Return the stable screening key, accepting candidate-id legacy rows."""

    return _first(row, "screening_id", "candidate_screening_id", "candidate_id")


def _final_candidates(rows: list[ArtifactRow]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        final_watchlist = row.row.get("final_watchlist")
        if not isinstance(final_watchlist, list):
            continue
        for candidate in final_watchlist:
            if isinstance(candidate, dict):
                result.append(candidate)
    return result


def _postseal_validated_final_ids(
    by_name: dict[str, list[ArtifactRow]],
    *,
    sealed_final_ids: set[str],
) -> set[str] | None:
    """Validate an explicit outcome-independent removal-only final repair."""

    receipt_rows = by_name.get("postseal_semantic_repair_receipt.json", [])
    if len(receipt_rows) != 1 or not sealed_final_ids:
        return None
    receipt = receipt_rows[0].row
    removed_rows = receipt.get("removed_candidates")
    if not isinstance(removed_rows, list) or not removed_rows:
        return None
    if (
        receipt.get("outcome_independent") is not True
        or receipt.get("outcome_metrics_used_to_remove_or_rank") is not False
        or _string_list(receipt.get("outcome_snapshot_fields_read_by_repair"))
        or _int(receipt.get("replacement_candidate_count"), default=-1) != 0
        or _int(receipt.get("sealed_final_watchlist_count"), default=-1)
        != len(sealed_final_ids)
        or _int(receipt.get("removed_count"), default=-1) != len(removed_rows)
    ):
        return None

    removed_ids = {
        candidate_id
        for row in removed_rows
        if isinstance(row, dict)
        for candidate_id in [_first(row, "candidate_id")]
        if candidate_id is not None
    }
    if len(removed_ids) != len(removed_rows) or not removed_ids <= sealed_final_ids:
        return None

    semantic_rows_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_name.get("candidate_semantic_witness.jsonl", []):
        candidate_id = _first(row.row, "candidate_id")
        if candidate_id is not None:
            semantic_rows_by_candidate[candidate_id].append(row.row)
    ranking_rows_by_candidate: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in by_name.get("candidate_ranking_audit.jsonl", []):
        candidate_id = _first(row.row, "candidate_id")
        if candidate_id is not None:
            ranking_rows_by_candidate[candidate_id].append(row.row)

    for removed in removed_rows:
        if not isinstance(removed, dict):
            return None
        candidate_id = _first(removed, "candidate_id")
        semantic_matches = semantic_rows_by_candidate.get(candidate_id or "", [])
        ranking_matches = ranking_rows_by_candidate.get(candidate_id or "", [])
        if len(semantic_matches) != 1 or len(ranking_matches) != 1:
            return None
        semantic = semantic_matches[0]
        ranking = ranking_matches[0]
        repair_reason = _string(removed.get("repair_reason"))
        if (
            semantic.get("final_eligible") is not False
            or semantic.get("pass") is not False
            or str(semantic.get("semantic_verdict") or "").upper() != "FAIL"
            or repair_reason is None
            or repair_reason not in _string_list(semantic.get("fail_reasons"))
            or ranking.get("included_in_final") is not False
            or ranking.get("postseal_semantic_repair_outcome_independent") is not True
            or not _string(ranking.get("why_not_final_if_excluded"))
            or _string_list(removed.get("outcome_fields_used"))
        ):
            return None

    included_ids = {
        candidate_id
        for candidate_id, rows in ranking_rows_by_candidate.items()
        if len(rows) == 1 and rows[0].get("included_in_final") is True
    }
    if included_ids != sealed_final_ids - removed_ids:
        return None
    if _int(receipt.get("validated_final_watchlist_count"), default=-1) != len(
        included_ids
    ):
        return None
    included_ranks = sorted(
        _int(rows[0].get("rank_if_final_or_null"), default=-1)
        for candidate_id, rows in ranking_rows_by_candidate.items()
        if candidate_id in included_ids
    )
    if included_ranks != list(range(1, len(included_ids) + 1)):
        return None
    return included_ids


def _final_candidate_ids(rows: list[ArtifactRow]) -> set[str]:
    return {
        candidate_id
        for candidate in _final_candidates(rows)
        for candidate_id in [_first(candidate, "candidate_id")]
        if candidate_id is not None
    }


def _resolved_final_relation_ids(
    rows: list[ArtifactRow],
    *,
    final_candidates: list[dict[str, Any]],
    final_witness_rows: list[ArtifactRow] | None = None,
) -> tuple[set[str], int]:
    """Resolve legacy final witness rows without inventing candidate identity."""
    final_candidate_ids = {
        candidate_id
        for candidate in final_candidates
        for candidate_id in [_first(candidate, "candidate_id")]
        if candidate_id is not None
    }
    candidate_by_screening: dict[str, str] = {}
    for candidate in final_candidates:
        candidate_id = _first(candidate, "candidate_id")
        screening_id = _first(candidate, "source_screening_id", "screening_id")
        if candidate_id is None or screening_id is None:
            continue
        prior = candidate_by_screening.get(screening_id)
        if prior is None:
            candidate_by_screening[screening_id] = candidate_id
        elif prior != candidate_id:
            # Ambiguous screening aliases must not be used to invent a final
            # candidate join.
            candidate_by_screening.pop(screening_id, None)
    witness_to_candidate: dict[str, str] = {}
    ambiguous_witness_ids: set[str] = set()
    for witness_row in final_witness_rows or []:
        witness_id = _first(
            witness_row.row,
            "final_evidence_witness_id",
            "witness_id",
        )
        candidate_id = _first(witness_row.row, "candidate_id")
        if candidate_id is None:
            candidate_id = candidate_by_screening.get(
                _first(witness_row.row, "candidate_screening_id", "source_screening_id")
                or ""
            )
        if witness_id is None or candidate_id is None:
            continue
        previous = witness_to_candidate.get(witness_id)
        if previous is not None and previous != candidate_id:
            ambiguous_witness_ids.add(witness_id)
            continue
        witness_to_candidate[witness_id] = candidate_id
    resolved: list[str] = []
    for artifact_row in rows:
        row = artifact_row.row
        explicit = _first(row, "candidate_id")
        if explicit is None:
            watchlist_id = _first(row, "watchlist_id", "final_watchlist_id")
            if watchlist_id is not None:
                watchlist_matches = [
                    candidate_id
                    for candidate in final_candidates
                    for candidate_id in [_first(candidate, "candidate_id")]
                    if _first(candidate, "watchlist_id", "final_watchlist_id") == watchlist_id
                    and candidate_id is not None
                ]
                if len(watchlist_matches) == 1:
                    resolved.append(watchlist_matches[0])
                continue
        if explicit is None:
            screening_id = _first(
                row,
                "candidate_screening_id",
                "source_screening_id",
                "screening_id",
            )
            if screening_id is not None:
                candidate_id = candidate_by_screening.get(screening_id)
                if candidate_id in final_candidate_ids:
                    resolved.append(candidate_id)
                    continue
            witness_id = _first(
                row,
                "final_evidence_witness_id",
                "witness_id",
            )
            if witness_id is not None and witness_id not in ambiguous_witness_ids:
                candidate_id = witness_to_candidate.get(witness_id)
                if candidate_id in final_candidate_ids:
                    resolved.append(candidate_id)
                continue
        if explicit is not None:
            # Some bundles use the final-semantic artifact as a full
            # candidate-semantic audit (one row per screened candidate).  Only
            # rows whose explicit candidate is in the sealed final watchlist
            # satisfy the final relation; the broader audit rows are retained
            # but must not appear as spurious final-witness extras.
            if explicit in final_candidate_ids:
                resolved.append(explicit)
            continue

        rank = row.get("rank", row.get("final_rank"))
        ticker = _first(row, "ticker", "code")
        fact_id = _first(row, "primary_fact_id", "fact_id")
        screening_id = _first(row, "source_screening_id", "screening_id")
        if rank is None or ticker is None:
            continue

        matches: list[str] = []
        for candidate in final_candidates:
            candidate_id = _first(candidate, "candidate_id")
            if candidate_id is None or not _relation_scalar_equal(rank, candidate.get("rank")):
                continue
            if ticker != _first(candidate, "ticker", "code"):
                continue
            if fact_id is not None and fact_id not in _field_string_values(
                    candidate,
                    "source_fact_ids",
                    "fact_ids",
                    "primary_fact_id",
                    "fact_id",
                ):
                    continue
            candidate_screening_id = _first(
                candidate,
                "source_screening_id",
                "screening_id",
            )
            if screening_id is not None and candidate_screening_id != screening_id:
                continue
            matches.append(candidate_id)
        if len(matches) == 1:
            resolved.append(matches[0])

    counts = Counter(resolved)
    duplicate_count = sum(count - 1 for count in counts.values() if count > 1)
    return set(resolved), duplicate_count


def _recursive_string_values(value: Any, field: str) -> set[str]:
    values: set[str] = set()
    if isinstance(value, dict):
        candidate = value.get(field)
        if isinstance(candidate, str) and candidate:
            values.add(candidate)
        for nested in value.values():
            values.update(_recursive_string_values(nested, field))
    elif isinstance(value, list):
        for nested in value:
            values.update(_recursive_string_values(nested, field))
    return values


def _canonical_case_ids(record: dict[str, Any], field: str) -> set[str]:
    values = _field_string_values(record, field)
    payload = record.get("payload")
    if isinstance(payload, dict):
        values.update(_field_string_values(payload, field))
    return values


def _case_population_record_ids(
    record: dict[str, Any],
    category: str,
    identifier_fields: tuple[str, ...],
) -> set[str]:
    values = {
        value
        for field in identifier_fields
        for value in _canonical_case_ids(record, field)
    }
    if category == "RANKING":
        # Reverse postmortem rows often retain the source ranking case as
        # ``RRE-*`` in payload instead of repeating the case block's ``REC-*``.
        values.update(_recursive_string_values(record, "reverse_ranking_error_case_id"))
        values.update(_recursive_string_values(record, "ranking_error_case_id"))
    # Repair-derived case rows keep the authoritative source artifact ID in a
    # derivation witness rather than duplicating it as a top-level field.  The
    # witness is explicit lineage evidence, so accepting its source_case_id is
    # lossless and does not invent a case judgment.
    derivations = record.get("repair_population_derivations")
    if isinstance(derivations, list):
        for derivation in derivations:
            if isinstance(derivation, dict):
                source_case_id = _string(derivation.get("source_case_id"))
                if source_case_id is not None:
                    values.add(source_case_id)
    return values


def _case_population_identity_key(category: str, value: str) -> str:
    if category == "RANKING":
        match = re.fullmatch(
            r"(?:REC|RRE|RANKING(?:_ERROR)?|RANK(?:ING)?_ERROR)[-_]?(\d+)",
            value.upper(),
        )
        if match is not None:
            return f"RANKING:{match.group(1)}"
    if category == "PAIR":
        match = re.fullmatch(
            r"(?:PAIR|PAIRCASE|BLIND[_-]?PAIR)[-_]?(\d+)",
            value.upper(),
        )
        if match is not None:
            return f"PAIR:{int(match.group(1))}"
    return value


def _case_population_identity_matches(
    category: str,
    required_id: str,
    actual_ids: set[str],
) -> bool:
    required_key = _case_population_identity_key(category, required_id)
    return any(
        _case_population_identity_key(category, actual_id) == required_key
        for actual_id in actual_ids
    )


def _leader_policy_thresholds(
    leader_rows: list[ArtifactRow],
    *,
    policy_rows: list[ArtifactRow] | None = None,
) -> tuple[int | None, int | None, float, int | None]:
    amount_values: set[int] = set()
    turnover_values: set[int] = set()
    high_return_values: set[int] = set()
    high_return_rank_values: set[int] = set()
    for policy_row in policy_rows or []:
        # Policy evidence appears in several legacy shapes, including
        # ``leader_census.class_counts`` where the threshold is encoded in
        # object keys (for example ``AMOUNT_TOP20``), not in a field named
        # ``leader_policy``.  Restrict the recursive scan to explicitly
        # policy-shaped fields so unrelated postmortem prose/counts cannot
        # silently change the leader universe.
        for field, value in policy_row.row.items():
            normalized_field = field.lower()
            is_policy_field = normalized_field in {
                "leader_policy",
                "leader_census",
                "leader_membership",
                "leader_population",
                "leader_band_counts",
                "cohort_policy",
                "outcome_leader_policy",
                "outcome_population_audit",
            } or (
                "policy" in normalized_field
                and any(token in normalized_field for token in ("leader", "census", "outcome"))
            )
            if is_policy_field:
                _collect_leader_policy_thresholds(
                    value,
                    amount_values=amount_values,
                    turnover_values=turnover_values,
                    high_return_values=high_return_values,
                    high_return_rank_values=high_return_rank_values,
                )
    for row in leader_rows:
        for field in _LEADER_MEMBERSHIP_FIELDS:
            # Row classification such as LIQUIDITY_LEADER_HIGH5 is not a
            # global leader-census threshold.  Only explicit policy fields or
            # inclusion rules may define the threshold.
            if field == "outcome_class":
                outcome_class = str(row.row.get(field) or "").upper()
                if not any(token in outcome_class for token in ("AMOUNT", "TURNOVER", "TOP")):
                    continue
            if field not in row.row:
                continue
            if field == "amount_rank_top_group_threshold":
                _add_positive_int(amount_values, row.row[field])
                continue
            if field == "turnover_rank_top_group_threshold":
                _add_positive_int(turnover_values, row.row[field])
                continue
            _collect_leader_policy_thresholds(
                row.row[field],
                amount_values=amount_values,
                turnover_values=turnover_values,
                high_return_values=high_return_values,
                high_return_rank_values=high_return_rank_values,
            )
    amount_top_n = next(iter(amount_values)) if len(amount_values) == 1 else None
    turnover_top_n = next(iter(turnover_values)) if len(turnover_values) == 1 else None
    # Current policy defaults to HIGH10. Legacy bundles may declare a broader
    # leader universe (for example HIGH5); the smallest cumulative threshold is
    # the actual inclusion boundary while HIGH15/HIGH20 remain nested labels.
    high_return_threshold = float(min(high_return_values, default=10))
    high_return_rank_top_n = (
        min(high_return_rank_values) if len(high_return_rank_values) == 1 else None
    )
    return amount_top_n, turnover_top_n, high_return_threshold, high_return_rank_top_n


def _collect_leader_policy_thresholds(
    value: Any,
    *,
    amount_values: set[int],
    turnover_values: set[int],
    high_return_values: set[int],
    high_return_rank_values: set[int],
) -> None:
    if isinstance(value, dict):
        for field, nested in value.items():
            normalized_field = re.sub(r"[^A-Z0-9]+", "_", field.upper()).strip("_")
            _collect_leader_policy_token(
                normalized_field,
                amount_values=amount_values,
                turnover_values=turnover_values,
                high_return_values=high_return_values,
                high_return_rank_values=high_return_rank_values,
            )
            if normalized_field in {
                "AMOUNT_RANK_TOP_GROUP",
                "AMOUNT_RANK_TOP_GROUP_N",
                "AMOUNT_RANK_TOP_N",
                "AMOUNT_RANK_TOP_GROUP_MAX",
                "AMOUNT_RANK_TOP_GROUP_MAX_RANK",
                "AMOUNT_RANK_TOP_MAX",
                "AMOUNT_TOP_GROUP_MAX",
                "AMOUNT_TOP_N",
            }:
                _add_positive_int(amount_values, nested)
            elif normalized_field in {
                "TURNOVER_RANK_TOP_GROUP",
                "TURNOVER_RANK_TOP_GROUP_N",
                "TURNOVER_RANK_TOP_N",
                "TURNOVER_RANK_TOP_GROUP_MAX",
                "TURNOVER_RANK_TOP_GROUP_MAX_RANK",
                "TURNOVER_RANK_TOP_MAX",
                "TURNOVER_TOP_GROUP_MAX",
                "TURNOVER_TOP_N",
            }:
                _add_positive_int(turnover_values, nested)
            elif normalized_field in {
                "HIGH_RETURN_THRESHOLD",
                "HIGH_RETURN_THRESHOLDS",
                "HIGH_RETURN_FLOOR",
                "HIGH_RETURN_FLOOR_PCT",
                "MINIMUM_HIGH_RETURN_PCT",
                "MIN_HIGH_RETURN_PCT",
            }:
                for threshold in nested if isinstance(nested, list) else [nested]:
                    if (
                        isinstance(threshold, (int, float))
                        and not isinstance(threshold, bool)
                        and threshold > 0
                        and float(threshold).is_integer()
                    ):
                        high_return_values.add(int(threshold))
            elif normalized_field in {
                "HIGH_RETURN_RANK_TOP_N",
                "HIGH_RETURN_TOP_N",
                "HIGH_RETURN_TOP_N_CLEAN",
            }:
                _add_positive_int(high_return_rank_values, nested)
            if nested is True:
                _collect_leader_policy_token(
                    normalized_field,
                    amount_values=amount_values,
                    turnover_values=turnover_values,
                    high_return_values=high_return_values,
                    high_return_rank_values=high_return_rank_values,
                )
            elif isinstance(nested, (dict, list, str)):
                _collect_leader_policy_thresholds(
                    nested,
                    amount_values=amount_values,
                    turnover_values=turnover_values,
                    high_return_values=high_return_values,
                    high_return_rank_values=high_return_rank_values,
                )
        return
    if isinstance(value, list):
        for nested in value:
            _collect_leader_policy_thresholds(
                nested,
                amount_values=amount_values,
                turnover_values=turnover_values,
                high_return_values=high_return_values,
                high_return_rank_values=high_return_rank_values,
            )
        return
    if isinstance(value, str):
        _collect_leader_policy_token(
            value,
            amount_values=amount_values,
            turnover_values=turnover_values,
            high_return_values=high_return_values,
            high_return_rank_values=high_return_rank_values,
        )


def _collect_leader_policy_token(
    value: str,
    *,
    amount_values: set[int],
    turnover_values: set[int],
    high_return_values: set[int],
    high_return_rank_values: set[int],
) -> None:
    normalized = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
    patterns = (
        (amount_values, r"(?:AMOUNT(?:_RANK)?(?:_TOP)?|TOP_AMOUNT)_?(\d+)"),
        (amount_values, r"TOP_?(\d+)_AMOUNT"),
        (turnover_values, r"(?:TURNOVER(?:_RANK)?(?:_TOP)?|TOP_TURNOVER)_?(\d+)"),
        (turnover_values, r"TOP_?(\d+)_TURNOVER"),
        # Some leader censuses spell the high-return rank cohort as
        # ``TOP30_VERIFIED_HIGH_RETURN`` rather than
        # ``HIGH_RETURN_TOP_30``.  This is an evidence token, not a fixed
        # threshold; extract the declared N from either spelling.
        (high_return_rank_values, r"TOP_?(\d+)(?:_VERIFIED)?_HIGH(?:_RETURN)?"),
        (high_return_rank_values, r"HIGH_RETURN(?:_RANK)?_TOP_?(\d+)"),
        (high_return_values, r"HIGH(?:_RETURN(?:_PCT)?)?(?:_GE)?_?(\d+)"),
    )
    for target, pattern in patterns:
        target.update(int(match) for match in re.findall(pattern, normalized))


def _leader_row_has_explicit_non_metric_membership(row: dict[str, Any]) -> bool:
    outcome_class = _string(row.get("outcome_class"))
    if outcome_class is not None:
        normalized_class = re.sub(r"[^A-Z0-9]+", "_", outcome_class.upper()).strip("_")
        if normalized_class in {
            "LIQUIDITY_TOP",
            "LIQUIDITY_TOP_GROUP",
            "LIQUIDITY_LEADER",
            "LIQUIDITY_LEADER_GROUP",
        }:
            return True
    for field in _LEADER_MEMBERSHIP_FIELDS:
        for value in _string_list(row.get(field)):
            normalized = re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")
            if normalized in {
                "FINAL_WATCHLIST_OUTCOME_JOIN",
                "SEALED_FINAL_WATCHLIST_OUTCOME_JOIN",
                "FINAL_WATCHLIST_JOIN",
            }:
                return True
    return False


def _add_positive_int(target: set[int], value: Any) -> None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        target.add(value)


def _outcome_nested_value(row: dict[str, Any], field: str) -> Any:
    if field in row and row[field] is not None:
        return row[field]
    # Outcome bundles legitimately nest the same snapshot fields under
    # domain sections (``returns``, ``ranks``, ``upper_limit`` and ``price``)
    # rather than flattening them.  Read only the named field from these
    # schema containers; this is an alias normalization, not a new leader
    # policy or a fabricated outcome value.
    for container_field in (
        "normalized",
        "data",
        "D_response",
        "d_response",
        "fields",
        "raw_snapshot_row",
        "snapshot_fields",
        "returns",
        "ranks",
        "upper_limit",
        "price",
        "activity",
        "history_flags",
    ):
        container = row.get(container_field)
        if isinstance(container, dict) and container.get(field) is not None:
            return container[field]
    return None


def _outcome_ticker(row: dict[str, Any]) -> str | None:
    return _string(_outcome_nested_value(row, "ticker")) or _string(_outcome_nested_value(row, "code"))


def _outcome_identity(row: dict[str, Any]) -> str | None:
    explicit = _outcome_explicit_identity(row)
    if explicit is not None:
        return explicit
    ticker = _outcome_ticker(row)
    return f"TICKER:{ticker}" if ticker is not None else None


def _outcome_explicit_identity(row: dict[str, Any]) -> str | None:
    for field in (
        "outcome_row_id",
        "outcome_id",
        "outcome_ledger_id",
        "source_outcome_id",
        "source_outcome_row_id",
    ):
        value = _string(_outcome_nested_value(row, field))
        if value is not None:
            return value
    return None


def _outcome_policy_leader(row: dict[str, Any]) -> bool | None:
    value = _outcome_nested_value(row, "policy_leader")
    return value if isinstance(value, bool) else None


def _outcome_row_is_explicitly_quarantined(row: dict[str, Any]) -> bool:
    """Return whether the row itself declares unusable price provenance.

    This is deliberately metadata-driven.  It does not infer exclusion from
    a low return or a rank; it only recognizes explicit quarantine/no-reference
    markers emitted by the outcome acquisition contract.
    """
    if _outcome_nested_value(row, "new_listing_or_no_reference") is True:
        return True
    for field in (
        "price_label_quality",
        "data_quality_status",
        "upper_limit_label_status",
    ):
        value = _outcome_nested_value(row, field)
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower()
        if any(
            token in normalized
            for token in ("quarantin", "blocked", "no_reference", "missing_reference")
        ):
            return True
    return False


def _outcome_row_requires_leader(
    row: dict[str, Any],
    *,
    amount_top_n: int | None,
    turnover_top_n: int | None,
    high_return_threshold: float,
    high_return_rank_top_n: int | None = None,
) -> bool:
    policy_leader = _outcome_policy_leader(row)
    if policy_leader is not None:
        return policy_leader
    return (
        _outcome_nested_value(row, "upper_limit_touched") is True
        or _float(_outcome_nested_value(row, "high_return_pct")) >= high_return_threshold
        or (
            high_return_rank_top_n is not None
            and 0 < _int(_outcome_nested_value(row, "high_return_rank"), default=0)
            <= high_return_rank_top_n
        )
        or (amount_top_n is not None and 0 < _int(_outcome_nested_value(row, "amount_rank"), default=0) <= amount_top_n)
        or (
            turnover_top_n is not None
            and 0 < _int(_outcome_nested_value(row, "turnover_rank"), default=0) <= turnover_top_n
        )
    )


def _duplicate_alias_key_count(
    rows: list[ArtifactRow],
    fields: tuple[str, ...],
) -> int:
    values = [value for row in rows for value in [_first(row.row, *fields)] if value]
    return sum(count - 1 for count in Counter(values).values() if count > 1)


def _negative_control_duplicate_key_count(rows: list[ArtifactRow]) -> int:
    """Count duplicate negative rows using group ID plus row-level evidence."""

    keys: list[tuple[str, str | None]] = []
    for row in rows:
        explicit_id = _first(row.row, "negative_control_id", "negative_control_case_id")
        group_id = explicit_id or _first(row.row, "case_id")
        if group_id is None:
            continue
        discriminator = _first(row.row, "source_screening_id", "screening_id")
        if discriminator is None:
            fact_values = _field_string_values(
                row.row,
                "source_fact_id",
                "fact_id",
                "source_fact_ids",
                "fact_ids",
            )
            discriminator = sorted(fact_values)[0] if fact_values else None
        keys.append((group_id, discriminator))
    return sum(count - 1 for count in Counter(keys).values() if count > 1)


def _final_watchlist_duplicate_count(rows: list[ArtifactRow]) -> int:
    candidates: list[dict[str, Any]] = []
    for row in rows:
        final_watchlist = row.row.get("final_watchlist")
        if isinstance(final_watchlist, list):
            candidates.extend(candidate for candidate in final_watchlist if isinstance(candidate, dict))
    duplicate_count = 0
    # Equal ranks are valid ties, especially when one event names multiple
    # listed issuers. Identity fields must remain unique; presentation order
    # is not a logical key.
    for fields in (
        ("watch_id", "watchlist_id"),
        ("candidate_id",),
        ("ticker", "stock_code", "code"),
    ):
        values = [
            value
            for candidate in candidates
            for value in [_first(candidate, *fields)]
            if value is not None
        ]
        duplicate_count += sum(count - 1 for count in Counter(values).values() if count > 1)
    return duplicate_count


def _duplicate_key_count(rows: list[ArtifactRow], field: str) -> int:
    values = [value for row in rows for value in [_first(row.row, field)] if value]
    return sum(count - 1 for count in Counter(values).values() if count > 1)


def _record_id(row: dict[str, Any]) -> str | None:
    return _first(row, "record_id", "brain_delta_id", "brain_record_id")


def _record_identity_values(row: dict[str, Any]) -> set[str]:
    """Return all explicit record-ID aliases used for lineage matching.

    Legacy bundles commonly identify a brain row with ``brain_record_id`` while
    the repaired canonical row also materializes ``record_id``/``brain_delta_id``.
    These are identity aliases, not new records.  Keep the namespacing suffix so
    an episode-scoped canonical ID can still join its legacy counterpart.
    """
    values: set[str] = set()
    for field in ("record_id", "brain_delta_id", "brain_record_id", "legacy_record_id"):
        value = _string(row.get(field))
        if value is None:
            continue
        values.add(value)
        if "__" in value:
            values.add(value.rsplit("__", 1)[-1])
    return values


def _legacy_identity_materialized(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    """Whether canonical IDs are safely materialized from a retained legacy ID."""
    legacy_id = _string(before.get("brain_record_id"))
    canonical_id = _string(after.get("record_id"))
    episode_id = _string(after.get("episode_id"))
    return bool(
        legacy_id
        and after.get("brain_record_id") == legacy_id
        and canonical_id
        and episode_id
        and canonical_id.startswith(f"{episode_id}__")
        and after.get("brain_delta_id") == canonical_id
    )


def _stable_brain_record_key(row: dict[str, Any]) -> str | None:
    """Use the retained legacy brain ID before episode-scoped aliases."""
    return _first(row, "brain_record_id", "record_id", "brain_delta_id")


def _record_references(value: Any) -> set[str]:
    references: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.endswith("_record_id") and isinstance(nested, str):
                references.add(nested)
            elif key.endswith("_record_ids") and isinstance(nested, list):
                references.update(item for item in nested if isinstance(item, str))
            else:
                references.update(_record_references(nested))
    elif isinstance(value, list):
        for nested in value:
            references.update(_record_references(nested))
    return references


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for key in sorted(set(before) | set(after)):
        if before.get(key) == after.get(key):
            continue
        changed[key] = {
            "before_sha256": sha256_text(canonical_json(before.get(key))),
            "after_sha256": sha256_text(canonical_json(after.get(key))),
        }
    return changed


def _reference_kind_for_field(field: str) -> str | None:
    normalized = field.lower()
    for reference_type in ("fact", "inference", "source"):
        if normalized == f"{reference_type}_id":
            return reference_type
        if normalized == f"{reference_type}_ids":
            return reference_type
        if normalized.endswith(f"_{reference_type}_id"):
            return reference_type
        if normalized.endswith(f"_{reference_type}_ids"):
            return reference_type
    return None


def _legacy_unresolved_reference_values(
    after: dict[str, Any],
    field: str,
) -> set[str]:
    reference_type = _reference_kind_for_field(field)
    if reference_type == "fact":
        return set(_string_list(after.get("legacy_unresolved_fact_tokens")))
    if reference_type == "inference":
        return set(_string_list(after.get("legacy_unresolved_inference_tokens")))
    if reference_type == "source":
        return set(_string_list(after.get("legacy_unresolved_source_tokens")))
    return set()


def _reference_strings(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str) and item}
    return set()


def _unresolved_reference_change_allowed(
    *,
    before_value: Any,
    after: dict[str, Any],
    field: str,
) -> bool:
    removed = _reference_strings(before_value)
    unresolved = _legacy_unresolved_reference_values(after, field)
    return bool(removed) and removed <= unresolved and bool(
        _string(after.get("unresolved_reference_reason"))
    )


def _nested_unresolved_reference_list_change_allowed(
    before: list[Any],
    after_value: list[Any],
    *,
    root_after: dict[str, Any],
) -> bool:
    """Allow a list-of-object edit when only unresolved typed refs were removed.

    Legacy bundles commonly place ``support_fact_ids`` or
    ``support_inference_ids`` inside a clause/witness list.  The repair keeps
    the clause text and all non-reference fields, removes only IDs absent from
    this bundle's ledgers, and records those IDs in the root legacy token
    fields.  Treating the whole list as replaced would falsely report an
    illegal transform.
    """

    def visit(old: Any, new: Any) -> bool:
        if isinstance(old, dict):
            if not isinstance(new, dict):
                return False
            for key, old_value in old.items():
                if key not in new:
                    if _reference_kind_for_field(str(key)) in {"fact", "inference", "source"}:
                        removed = _reference_strings(old_value)
                        unresolved = _legacy_unresolved_reference_values(root_after, str(key))
                        if removed and removed <= unresolved:
                            continue
                    if old_value in (None, "", [], {}):
                        continue
                    return False
                if (
                    _reference_kind_for_field(str(key)) in {"fact", "inference", "source"}
                    and isinstance(old_value, list)
                    and isinstance(new[key], list)
                ):
                    removed = _reference_strings(old_value) - _reference_strings(new[key])
                    unresolved = _legacy_unresolved_reference_values(root_after, str(key))
                    if (
                        _reference_strings(new[key]) <= _reference_strings(old_value)
                        and removed
                        and removed <= unresolved
                    ):
                        continue
                if not visit(old_value, new[key]):
                    return False
            return all(
                key in old or new_value in (None, "", [], {})
                for key, new_value in new.items()
            )
        if isinstance(old, list):
            return isinstance(new, list) and len(old) == len(new) and all(
                visit(left, right) for left, right in zip(old, new, strict=True)
            )
        return bool(old == new)

    return len(before) == len(after_value) and all(
        visit(left, right) for left, right in zip(before, after_value, strict=True)
    )


def _illegal_transform_paths(
    before: dict[str, Any],
    after: dict[str, Any] | None,
    *,
    artifact_name: str | None = None,
    verified_source_timestamp: str | None = None,
) -> list[str]:
    if after is None:
        return []
    illegal: list[str] = []
    source_reference_fields = {
        "source_id",
        "source_row_id",
        "source_ids",
        "source_row_ids",
        "provenance_source_ids",
        "source_ledger_ids",
        "matched_source_ids",
        "matched_source_row_ids",
        "news_source_id",
        "news_source_ids",
        "trigger_source_ids",
        "outcome_id",
        "outcome_ids",
        "outcome_ledger_id",
        "outcome_leader_id",
        "outcome_leader_ids",
        "leader_id",
        "leader_census_id",
        "material_review_id",
        "material_review_ids",
        "source_material_review_ids",
    }

    def walk(old: Any, new: Any, path: tuple[str, ...]) -> None:
        if (
            path == ("company_name",)
            and isinstance(old, dict)
            and isinstance(new, str)
            and after.get("legacy_company_name_payload") == old
            and new
            in _recursive_values_for_keys(
                old,
                ("company_name", "issuer_name", "name", "company"),
            )
        ):
            return
        if isinstance(old, dict):
            if not isinstance(new, dict):
                illegal.append(".".join(path) or "<root>")
                return
            for key, old_value in old.items():
                if key not in new:
                    if (
                        key in _EVENT_REFERENCE_FIELDS
                        and isinstance(old_value, list)
                        and any(item is None for item in old_value)
                        and not [item for item in old_value if item is not None]
                        and key
                        in _string_list(
                            after.get("repair_removed_null_event_reference_fields")
                        )
                    ):
                        continue
                    if key in _EVENT_REFERENCE_FIELDS and isinstance(old_value, list):
                        moved = _string_list(after.get("legacy_mistyped_event_reference_values"))
                        related_domain_ids = _string_list(after.get("related_domain_ids"))
                        old_values = set(_string_list(old_value))
                        moved_values = set(moved)
                        if (
                            old_values
                            and old_values <= moved_values
                            and old_values <= set(related_domain_ids)
                        ):
                            continue
                    if key == "direct_event_id" and isinstance(old_value, str):
                        moved = _string_list(after.get("legacy_mistyped_event_reference_values"))
                        if (
                            old_value in moved
                            and after.get("direct_event_fact_id") == old_value
                            and old_value in _string_list(after.get("related_domain_ids"))
                        ):
                            continue
                    if (
                        key in _SINGULAR_DOMAIN_EVENT_REFERENCE_FIELDS
                        and isinstance(old_value, str)
                        and old_value
                        in _string_list(after.get("legacy_mistyped_event_reference_values"))
                        and old_value in _string_list(after.get("related_domain_ids"))
                    ):
                        continue
                    if _unresolved_reference_change_allowed(
                        before_value=old_value,
                        after=after,
                        field=str(key),
                    ):
                        continue
                    if old_value in (None, "", [], {}):
                        continue
                    illegal.append(".".join((*path, key)))
                    continue
                walk(old_value, new[key], (*path, key))
            for key in new.keys() - old.keys():
                added_path = (*path, key)
                if not _is_allowed_added_field(
                    before,
                    after,
                    added_path,
                    new[key],
                    artifact_name=artifact_name,
                    verified_source_timestamp=verified_source_timestamp,
                ):
                    illegal.append(".".join(added_path))
            return
        if _relation_scalar_equal(old, new):
            return
        field = path[-1] if path else ""
        if isinstance(old, list) and isinstance(new, list):
            if (
                field in _EVENT_REFERENCE_FIELDS
                and field
                in _string_list(
                    after.get("repair_removed_null_event_reference_fields")
                )
                and any(item is None for item in old)
                and new == [item for item in old if item is not None]
            ):
                return
            if _nested_unresolved_reference_list_change_allowed(
                old,
                new,
                root_after=after,
            ):
                return
            if field == "provenance_source_ids" and _provenance_alias_list_change_allowed(
                before,
                after,
                old,
                new,
            ):
                return
            if (
                field in source_reference_fields
                and len(old) == len(new)
                and all(
                    _reference_identifier_alias_equivalent(left, right)
                    for left, right in zip(old, new, strict=True)
                )
            ):
                return
            if field in _EVENT_REFERENCE_FIELDS:
                moved = _string_list(after.get("legacy_mistyped_event_reference_values"))
                related_domain_ids = _string_list(after.get("related_domain_ids"))
                removed = set(_string_list(old)) - set(_string_list(new))
                if (
                    all(value in old for value in new)
                    and removed
                    and removed <= set(moved)
                    and removed <= set(related_domain_ids)
                ):
                    return
            if field == "related_domain_ids":
                moved = _string_list(after.get("legacy_mistyped_event_reference_values"))
                if all(value in new for value in old) and sorted(set(new) - set(old)) == sorted(set(moved)):
                    return
            if (
                field
                in {
                    "provenance_source_ids",
                    "source_fact_ids",
                    "source_inference_ids",
                }
                and field == "provenance_source_ids"
                and _event_ticker_edge_source_filter_valid(before, after)
            ):
                return
            if (
                field
                in {
                    "provenance_source_ids",
                    "source_fact_ids",
                    "source_inference_ids",
                }
                or field.endswith("_record_ids")
            ) and all(_value_or_namespaced_value_present(value, new) for value in old):
                return
            if _reference_kind_for_field(field) is not None:
                removed = set(old) - set(new)
                if (
                    all(item in old for item in new)
                    and removed
                    and removed <= _legacy_unresolved_reference_values(after, field)
                    and _string(after.get("unresolved_reference_reason"))
                ):
                    return
            illegal.append(".".join(path))
            return
        if _is_record_identity_field(field) and _is_namespaced_value(old, new):
            return
        if _numeric_identifier_scalar_equivalent(field, old, new):
            return
        if field in source_reference_fields and _reference_identifier_alias_equivalent(old, new):
            return
        if (
            field == "episode_id"
            and after.get("legacy_source_episode_id") == old
            and isinstance(new, str)
            and (_record_id(after) or "").startswith(f"{new}__")
        ):
                return
        if field == "ticker" and old in {None, ""} and isinstance(new, str):
            pair_aliases = set(
                _string_list(before.get("related_tickers"))
            )
            payload = before.get("payload")
            if isinstance(payload, dict):
                pair_aliases.update(_string_list(payload.get("related_tickers")))
            pair_aliases.update(
                _recursive_values_for_keys(
                    before,
                    ("left_ticker", "right_ticker"),
                )
            )
            if len(pair_aliases) == 1 and new in pair_aliases:
                return
        if field == "available_from":
            # Legacy postmortem rows sometimes use the phase name itself as
            # the availability value (or omit it). Repair replaces that
            # placeholder with the deterministic post-trade availability
            # timestamp from episode metadata. Accept only a timezone-aware
            # value strictly after the record's trade date; an arbitrary
            # future timestamp is not a legal normalization.
            old_placeholder = old in {
                None,
                "",
                "POSTMORTEM",
                "POSTSEAL",
                "POSTSEAL_SUPERVISED",
                "OUTCOME",
                "RETROSPECTIVE",
            }
            if (
                isinstance(old, str)
                and _parse_datetime_or_none(old) is None
                and any(
                    token in old.upper()
                    for token in ("POSTSEAL", "POSTMORTEM", "OUTCOME", "RETRO")
                )
            ):
                # Legacy bundles often encode availability as a phase label
                # such as POSTSEAL_OUTCOME rather than a timestamp. Treat
                # those labels like the explicit placeholders above, but keep
                # the strict post-trade/timezone checks below.
                old_placeholder = True
            new_dt = _parse_datetime_or_none(new)
            trade_dt = _parse_datetime_or_none(
                after.get("trade_date") or before.get("trade_date")
            )
            phase = str(
                after.get("source_phase") or before.get("source_phase") or ""
            ).upper()
            if (
                old_placeholder
                and new_dt is not None
                and new_dt.tzinfo is not None
                and trade_dt is not None
                and trade_dt.date() < new_dt.date()
                and any(token in phase for token in ("POST", "OUTCOME", "RETRO"))
            ):
                return
        if (
            field == "direct_event_id"
            and isinstance(old, str)
            and after.get("direct_event_fact_id") == old
            and old in _string_list(after.get("legacy_mistyped_event_reference_values"))
        ):
            return
        if field == "priority" and after.get("record_type") == "research_question":
            try:
                if Decimal(str(old)) == Decimal(str(new)):
                    return
            except (InvalidOperation, ValueError):
                pass
        if (
            field == "record_type"
            and after.get("legacy_record_type") == old
            and _known_record_type_transition(old, new)
        ):
            return
        if (
            artifact_name == "final_semantic_audit.jsonl"
            and field in {"status", "semantic_audit_status", "semantic_verdict"}
            and new == "PASS"
            and _source_semantic_row_passes(before)
        ):
            return
        if field in {"company_name", "ticker"} and new in _recursive_values_for_keys(
            before,
            (field, "company" if field == "company_name" else field, "name" if field == "company_name" else "code"),
        ):
            return
        if (
            field == "D_outcome"
            and old is None
            and new == {}
            and after.get("training_eligible") is False
            and _float(after.get("sample_weight")) == 0.0
            and (
                after.get("training_exclusion_reason")
                in {
                    "NO_TRADABLE_ROW",
                    "no_tradable_row",
                    "NO_TRADABLE_ROW_ON_D",
                    "no_tradable_row_on_d",
                    "NO_VERIFIED_D_OUTCOME_ROW",
                    "no_verified_D_outcome_row",
                }
                or (
                    isinstance(after.get("payload"), dict)
                    and after["payload"].get("response_class")
                    in {"NO_TRADABLE_ROW", "no_tradable_row", "NO_TRADABLE_ROW_ON_D", "no_tradable_row_on_d"}
                )
            )
        ):
            return
        if (
            field == "training_eligible"
            and old in {None, "", True}
            and new is False
            and after.get("sample_weight") == 0.0
            and _string(after.get("training_exclusion_reason")) is not None
        ):
            return
        if (
            field in {"training_exclusion_reason", "eligibility_reason"}
            and after.get("training_eligible") is False
            and _float(after.get("sample_weight")) == 0.0
            and _string(after.get("training_exclusion_reason")) is not None
            and (
                field == "training_exclusion_reason"
                or (
                    old in {None, ""}
                    or isinstance(old, str)
                )
                and isinstance(new, str)
                and (
                    old in {None, ""}
                    or new.startswith(old)
                )
                and str(after["training_exclusion_reason"]) in new
            )
        ):
            return
        if field == "training_target" and old in {None, ""} and new in _KNOWN_TRAINING_TARGETS:
            return
        if (
            artifact_name == "source_ledger.jsonl"
            and field in {"published_at", "published_at_kst"}
            and old in {None, ""}
            and isinstance(new, str)
        ):
            timestamp_provenance = after.get("timestamp_repair_provenance")
            if (
                isinstance(timestamp_provenance, dict)
                and timestamp_provenance.get("rule_id")
                == NEWS_TIMESTAMP_REPAIR_RULE
                and timestamp_provenance.get("published_at") == new
                and verified_source_timestamp == new
            ):
                return
        if (
            artifact_name == "source_ledger.jsonl"
            and field == "time_verified"
            and new is True
            and verified_source_timestamp == _first(
                after,
                "published_at_kst",
                "published_at",
            )
        ):
            return
        if (
            artifact_name == "source_ledger.jsonl"
            and field == "available_before_cutoff"
            and isinstance(new, bool)
            and verified_source_timestamp is not None
            and after.get("time_verified") is True
        ):
            return
        if field == "sample_weight":
            if (
                before.get("training_eligible") is True
                and after.get("training_eligible") is True
                and before.get("sample_weight") in {None, ""}
                and _float(new) == 1.0
            ):
                return
            if (
                after.get("training_eligible") is False
                and _float(new) == 0.0
                and _string(after.get("training_exclusion_reason")) is not None
            ):
                return
            if (
                after.get("issuer_day_sample_weight_policy") == "fractional_issuer_day_group"
                and 0.0 < _float(new) <= 1.0
            ):
                return
            illegal.append(".".join(path))
            return
        if field == "path_type" and new == _expected_path_type(before):
            return
        if field == "relation_class" and new == _expected_path_type(before):
            return
        if field == "issuer_day_sample_weight_policy" and new in {
            "fractional_issuer_day_group",
            "single_issuer_day_case",
        }:
            return
        if (
            field == "issuer_day_weight_group_id"
            and isinstance(old, str)
            and isinstance(new, str)
            and old.replace("|", ":") == new
        ):
            return
        if (
            field == "issuer_day_case_id"
            and isinstance(old, str)
            and isinstance(new, str)
            and old.replace("|", ":") == new
        ):
            return
        population_derivations = after.get("repair_population_derivations")
        if (
            field == "issuer_day_case_id"
            and isinstance(population_derivations, list)
            and any(
                isinstance(item, dict)
                and item.get("rule_id") == "case_id_from_unique_evidence_join.v2"
                and item.get("target_field") == field
                and item.get("source_case_id") == new
                for item in population_derivations
            )
        ):
            return
        if (
            field == "issuer_day_weight_group_id"
            and isinstance(new, str)
            and after.get("issuer_day_sample_weight_policy") == "fractional_issuer_day_group"
            and new
            == f"{after.get('trade_date') or ''}:{after.get('ticker') or ''}"
        ):
            return
        illegal.append(".".join(path))

    walk(before, after, ())
    return sorted(set(illegal))


def _numeric_identifier_scalar_equivalent(field: str, old: Any, new: Any) -> bool:
    """Allow lossless JSON-number to string conversion for identifier fields."""
    allowed_fields = {
        "row_id",
        "source_row_id",
        "input_row_id",
        "direct_event_id",
        "direct_event_case_id",
        "candidate_event_id",
    }
    if field not in allowed_fields:
        return False
    if isinstance(old, int) and not isinstance(old, bool) and isinstance(new, str):
        return new == str(old)
    if isinstance(new, int) and not isinstance(new, bool) and isinstance(old, str):
        return old == str(new)
    return False


def _source_reference_alias_equivalent(
    old: Any,
    new: Any,
    source_ledger_ids: set[str] | None = None,
) -> bool:
    """Recognize only the known numeric source-id spelling aliases."""
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return False
    old_match = _SOURCE_REFERENCE_ID_PATTERN.fullmatch(old)
    new_match = _SOURCE_REFERENCE_ID_PATTERN.fullmatch(new)
    if (
        old_match is None
        or new_match is None
        or old_match.group(1) != new_match.group(1)
    ):
        return False
    if source_ledger_ids is None:
        return True
    matching_targets = {
        source_id
        for source_id in source_ledger_ids
        if (match := _SOURCE_REFERENCE_ID_PATTERN.fullmatch(source_id)) is not None
        and match.group(1) == old_match.group(1)
    }
    return matching_targets == {new}


def _reference_identifier_alias_equivalent(
    old: Any,
    new: Any,
    source_ledger_ids: set[str] | None = None,
) -> bool:
    if _source_reference_alias_equivalent(old, new, source_ledger_ids):
        return True
    if _outcome_identifier_alias_equivalent(old, new):
        return True
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return False
    old_match = re.fullmatch(r"MREV-(\d+)", old)
    new_match = re.fullmatch(r"MRV-(\d+)", new)
    return bool(
        old_match is not None
        and new_match is not None
        and old_match.group(1) == new_match.group(1)
    )


def _outcome_identifier_alias_equivalent(old: Any, new: Any) -> bool:
    if not isinstance(old, str) or not isinstance(new, str) or old == new:
        return False
    for prefix in ("LEAD", "OUT"):
        old_match = re.fullmatch(rf"{prefix}-(?:[^-]+-)?0*(\d+)", old)
        new_match = re.fullmatch(rf"{prefix}-(?:[^-]+-)?0*(\d+)", new)
        if old_match is not None and new_match is not None:
            return int(old_match.group(1)) == int(new_match.group(1))
    return False


def _event_ticker_edge_source_filter_valid(
    before: dict[str, Any],
    after: dict[str, Any],
) -> bool:
    if before.get("record_type") != "event_ticker_edge":
        return False
    # Retrospective discovery rows are also produced after the outcome
    # snapshot.  The repair renderer may remove that post-outcome source from
    # an otherwise cutoff-safe event edge; the same provenance-filter contract
    # applies to both legacy phase labels.
    if str(before.get("source_phase") or "").upper() not in {
        "POSTMORTEM",
        "RETROSPECTIVE_DISCOVERY",
    }:
        return False
    metadata = after.get("provenance_source_filter")
    if not isinstance(metadata, dict):
        return False
    if metadata.get("rule_id") != "event_ticker_edge_cutoff_safe_sources.v1":
        return False
    before_sources = set(_string_list(before.get("provenance_source_ids")))
    after_sources = set(_string_list(after.get("provenance_source_ids")))
    removed_sources = set(_string_list(metadata.get("removed_source_ids")))
    retained_sources = set(_string_list(metadata.get("retained_source_ids")))
    return bool(
        before_sources
        and after_sources
        and after_sources < before_sources
        and removed_sources == before_sources - after_sources
        and retained_sources == after_sources
    )


_KNOWN_TRAINING_TARGETS = {
    "candidate_exclusion_calibration",
    "candidate_generation_correction",
    "candidate_ranking_correction",
    "context_market_state_or_fact",
    "direct_event_price_response",
    "issuer_day_price_response",
    "newsless_outcome_calibration",
    "outcome_preferred_candidate",
    "theme_formation_response",
}

_OUTCOME_LEADER_MISS_ALIASES = {
    "false_negative_outcome_leader",
    "missed_leader_error_case",
    "missed_outcome_leader",
    "missed_outcome_leader_analysis",
    "missed_outcome_leader_audit",
    "outcome_leader_census_supervised",
    "outcome_leader_day",
    "outcome_leader_news_match",
}


def _is_allowed_added_field(
    before: dict[str, Any],
    after: dict[str, Any],
    path: tuple[str, ...],
    value: Any,
    *,
    artifact_name: str | None,
    verified_source_timestamp: str | None = None,
) -> bool:
    # The repair renderer walks nested payload containers as well as the
    # record root.  When a legacy ``payload.event_ids`` list contains IDs that
    # are actually screenings/observations, it moves those exact values to
    # ``payload.screening_ids`` (or the corresponding domain alias).  Treat
    # that as the same source-anchored type normalization as the root-level
    # alias below; do not require callers to flatten the payload first.
    if len(path) >= 2 and path[-1] in {
        "screening_ids",
        "selected_blind_screening_ids",
        "missed_more_relevant_domain_ids",
        "sealed_domain_ids",
    }:
        parent: Any = before
        for segment in path[:-1]:
            if not isinstance(parent, dict) or segment not in parent:
                parent = None
                break
            parent = parent[segment]
        if isinstance(parent, dict):
            destination = path[-1]
            moved_by_source: set[str] = set()
            for source_field, destination_field in {
                "related_event_ids": "related_domain_ids",
                "selected_blind_event_ids": "selected_blind_screening_ids",
                "all_event_ids": "screening_ids",
                "event_ids": "screening_ids",
                "missed_more_relevant_event_ids": "missed_more_relevant_domain_ids",
                "sealed_event_ids": "sealed_domain_ids",
            }.items():
                if destination_field == destination:
                    moved_by_source.update(_string_list(parent.get(source_field)))
            moved_domain_ids = set(
                _string_list(after.get("legacy_mistyped_event_reference_values"))
            )
            moved_destination_values = set(_string_list(value))
            if (
                moved_destination_values
                and moved_domain_ids
                and moved_destination_values <= moved_domain_ids
                and moved_destination_values <= moved_by_source
            ):
                return True
    if len(path) >= 2 and path[-1] in set(
        _SINGULAR_DOMAIN_EVENT_REFERENCE_FIELDS.values()
    ):
        domain_parent: Any = before
        for segment in path[:-1]:
            if not isinstance(domain_parent, dict) or segment not in domain_parent:
                domain_parent = None
                break
            domain_parent = domain_parent[segment]
        singular_source_field: str | None = next(
            (
                field
                for field, destination in _SINGULAR_DOMAIN_EVENT_REFERENCE_FIELDS.items()
                if destination == path[-1]
            ),
            None,
        )
        return bool(
            isinstance(domain_parent, dict)
            and singular_source_field is not None
            and value == domain_parent.get(singular_source_field)
            and value in _string_list(after.get("legacy_mistyped_event_reference_values"))
            and value in _string_list(after.get("related_domain_ids"))
        )
    if len(path) != 1:
        return False
    field = path[0]
    before_type = _string(before.get("record_type"))
    after_type = _string(after.get("record_type"))
    normalized_before_type = before_type.strip().lower() if before_type else None
    payload = before.get("payload")
    if isinstance(payload, dict) and field in payload and payload[field] == value:
        return True
    if field == "legacy_company_name_payload":
        original_company_name = before.get("company_name")
        return bool(
            isinstance(original_company_name, dict)
            and value == original_company_name
            and after.get("company_name")
            in _recursive_values_for_keys(
                original_company_name,
                ("company_name", "issuer_name", "name", "company"),
            )
        )
    nested_question = payload.get("question") if isinstance(payload, dict) else None
    if (
        before_type == "research_question"
        and isinstance(nested_question, dict)
        and field in nested_question
        and nested_question[field] == value
    ):
        return True
    if (
        field == "label_quality"
        and isinstance(value, str)
        and isinstance(payload, dict)
        and isinstance(payload.get("label_quality"), str)
    ):
        source_label = payload["label_quality"].lower()
        expected_label = "verified" if source_label == "verified_normal_day" else source_label
        return value.lower() == expected_label
    if field == "legacy_unresolved_source_tokens":
        before_sources = {
            *_string_list(before.get("provenance_source_ids")),
            *_string_list(before.get("source_ids")),
            *_string_list(before.get("source_row_ids")),
        }
        return bool(_string_list(value)) and set(_string_list(value)) <= before_sources
    if field in {
        "screening_ids",
        "selected_blind_screening_ids",
        "missed_more_relevant_domain_ids",
    }:
        moved_ids = set(_string_list(after.get("legacy_mistyped_event_reference_values")))
        values = set(_string_list(value))
        return bool(values) and bool(moved_ids) and values <= moved_ids
    if field == "sealed_domain_ids":
        moved_ids = set(_string_list(after.get("legacy_mistyped_event_reference_values")))
        original_sealed = set(_string_list(before.get("sealed_event_ids")))
        return (
            bool(moved_ids)
            and moved_ids <= original_sealed
            and set(_string_list(value)) == moved_ids
        )
    if (
        field == "unresolved_reference_reason"
        and isinstance(value, str)
        and value == "source_reference_not_present_in_bundle_ledger"
        and _string_list(after.get("legacy_unresolved_source_tokens"))
    ):
        return True
    if (
        artifact_name == "source_ledger.jsonl"
        and field == "source_id"
        and "source_id" not in before
        and value == _first(before, "source_row_id", "row_id")
    ):
        # Legacy ledgers use source_row_id/row_id as the same stable identity
        # that newer records call source_id.  Adding the alias is structural
        # normalization, not a new provenance claim.
        return True
    if (
        artifact_name == "source_ledger.jsonl"
        and field in {"published_at", "published_at_kst"}
    ):
        # A verified legacy ledger may already carry one timestamp alias while
        # repair materializes the other.  Permit only the independently joined
        # instant; a different added timestamp remains an illegal transform.
        return (
            isinstance(value, str)
            and value == verified_source_timestamp
            and value == _first(after, "published_at_kst", "published_at")
        )
    if (
        artifact_name == "source_ledger.jsonl"
        and field == "time_verified"
    ):
        return (
            value is True
            and verified_source_timestamp
            == _first(after, "published_at_kst", "published_at")
        )
    if field == "trade_date" and isinstance(value, str):
        # A few legacy event rows keep the D-day only inside their outcome
        # object.  Promoting that source-anchored snapshot date to the
        # canonical trade_date is a representation change, not new evidence.
        for container_name in ("outcome", "D_response", "outcome_labels"):
            container = before.get(container_name)
            snapshot_dates = _recursive_values_for_keys(container, ("snapshot_date",))
            if snapshot_dates == {value}:
                return True
        # In some legacy bundles both fields are absent from the source row.
        # The repair step first creates a content-addressed episode namespace;
        # its YYYYMMDD token is still source-bound evidence for this mirror.
        episode_id = _string(before.get("episode_id") or after.get("episode_id"))
        compact_date = value.replace("-", "")
        if episode_id and re.search(rf"(?<!\d){re.escape(compact_date)}(?!\d)", episode_id):
            return True
    if before_type in {"issuer_day_outcome", "issuer_day_case"} and after_type == "supervised_issuer_day_case":
        if field == "issuer_day_case_id":
            return value == after.get("record_id") and _is_namespaced_value(
                _record_id(before), value
            )
        if field in {"D_outcome", "outcome"}:
            return _legacy_verified_outcome_matches_source(value, before)
        if field == "label_quality":
            return _string(value) == "verified" and _legacy_source_has_outcome(before)
        if field == "attribution_status":
            return _string(value) == "postseal_label_attached_to_sealed_final"
        if field == "safe_D1_features":
            return _derived_scalar_values_are_source_anchored(value, before)
    if before_type == "direct_event_case" and after_type == "supervised_direct_event_case":
        if field == "case_id":
            return value == after.get("record_id") and _is_namespaced_value(
                _record_id(before), value
            )
        if field == "issuer_day_case_id":
            return bool(value == f"{after.get('trade_date')}:{after.get('ticker')}")
        if field == "blind_fact_ids":
            return bool(value == after.get("source_fact_ids"))
        if field == "safe_D1_features":
            return _derived_scalar_values_are_source_anchored(value, before)
        if field in {"D_outcome", "outcome"}:
            return _legacy_verified_outcome_matches_source(value, before)
        if field == "response_class":
            return value in _recursive_values_for_keys(
                before,
                ("response_class", "response_label", "label"),
            )
        if field == "label_quality":
            return _string(value) == "verified" and _legacy_source_has_outcome(before)
        if field == "attribution_status":
            return _string(value) == "postseal_label_attached_to_sealed_direct_event"
    if before_type == "counterfactual_pair" and after_type == "blind_leader_preference_pair":
        if field == "blind_pair_id":
            return value == after.get("record_id") and _is_namespaced_value(
                _record_id(before), value
            )
        pair_aliases = {
            "blind_preferred_ticker": ("selected", "ticker"),
            "blind_preferred_company_name": ("selected", "issuer_name"),
            "blind_rejected_ticker": ("missed_leader", "ticker"),
            "blind_rejected_company_name": ("missed_leader", "issuer_name"),
            "outcome_winner_ticker": ("missed_leader", "ticker"),
            "outcome_winner_company_name": ("missed_leader", "issuer_name"),
        }
        if field in pair_aliases:
            container_name, source_field = pair_aliases[field]
            payload_dict = before.get("payload")
            container = (
                payload_dict.get(container_name)
                if isinstance(payload_dict, dict)
                else None
            )
            return isinstance(container, dict) and value == container.get(source_field)
        if field == "blind_preference_correct":
            return value is False
        if field == "training_mode":
            return bool(value == "postseal_counterfactual_pair")
        if field == "correction_mode":
            return value in _recursive_values_for_keys(before, ("comparison_axis",))
    if before_type == "outcome_leader_case" and after_type == "candidate_generation_error_case":
        payload_dict = before.get("payload")
        if not isinstance(payload_dict, dict):
            return False
        source_state = payload_dict.get("premarket_news_state")
        if payload_dict.get("blind_selected") is not False or source_state != "NEWS_PRESENT_NOT_SELECTED":
            return False
        if field == "error_id":
            return value == after.get("record_id") and _is_namespaced_value(
                _record_id(before), value
            )
        if field in {"error_type", "correction_mode"}:
            return bool(value == source_state)
        if field == "missed_ticker":
            return bool(value == payload_dict.get("ticker"))
        if field == "missed_company_name":
            return value in {
                payload_dict.get("company_name"),
                payload_dict.get("issuer_name"),
                payload_dict.get("name"),
            }
    if before_type == "blind_false_positive" and after_type == "negative_control_case":
        if field == "rejection_or_exclusion_reason":
            return isinstance(value, str) and value in {
                before.get("error_type"),
                before.get("error_reason"),
                "blind_false_positive",
            }
        if field == "outcome_high_return_pct":
            return _relation_scalar_equal(
                value,
                before.get("D_high_return_pct", before.get("high_return_pct")),
            )
    if before_type == "pairwise_rank_delta" and after_type == "ranking_error_case":
        if field == "error_id":
            return value == after.get("record_id") and _is_namespaced_value(
                _record_id(before), value
            )
        if field == "error_type":
            return _string(value) == "pairwise_rank_delta"
        if field == "correction":
            return isinstance(value, str) and value in {
                before.get("comparison_note"),
                before.get("correction"),
            }
        if field == "correction_mode":
            return _string(value) == "pairwise_rank_delta"
        if field == "corrected_ticker":
            return value in {before.get("winner_ticker"), before.get("ticker")}
        if field == "corrected_company_name":
            return value in {before.get("winner_company"), before.get("company")}
        if field == "outcome_high_return_pct":
            return _relation_scalar_equal(
                value,
                before.get("winner_high_return_pct", before.get("high_return_pct")),
            )
    if normalized_before_type in _OUTCOME_LEADER_MISS_ALIASES and after_type == "candidate_generation_error_case":
        if field == "error_id":
            return value == after.get("record_id") and _is_namespaced_value(
                _record_id(before),
                value,
            )
        if field == "error_type":
            return value in _recursive_values_for_keys(
                before,
                (
                    "classification",
                    "audit_result",
                    "audit_decision",
                    "label",
                    "error_mode",
                    "error_type",
                    "news_linkage_class",
                ),
            )
        if field == "missed_ticker":
            return value in _recursive_values_for_keys(before, ("ticker", "code"))
        if field == "missed_company_name":
            return value in _recursive_values_for_keys(before, ("company_name", "company", "name"))
        if field == "correction_mode":
            return value in _recursive_values_for_keys(
                before,
                ("correction_mode", "error_mode", "classification", "audit_result", "audit_decision"),
            )
    if field in {"legacy_unresolved_fact_tokens", "legacy_unresolved_inference_tokens"}:
        return (
            isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and bool(item) for item in value)
            and _string(after.get("unresolved_reference_reason"))
            in {
                "typed_reference_not_present_in_bundle_ledger",
                "source_reference_not_present_in_bundle_ledger",
            }
        )
    if field == "unresolved_reference_reason":
        return isinstance(value, str) and value in {
            "typed_reference_not_present_in_bundle_ledger",
            "source_reference_not_present_in_bundle_ledger",
        }
    if field == "episode_id":
        before_record_id = _record_id(before)
        after_record_id = _record_id(after)
        return (
            before.get("episode_id") in {None, ""}
            and isinstance(value, str)
            and bool(value)
            and before_record_id is not None
            and after_record_id is not None
            and after_record_id.startswith(f"{value}__")
            and (
                _is_namespaced_value(before_record_id, after_record_id)
                or _legacy_identity_materialized(before, after)
            )
        )
    if field in {"record_id", "brain_delta_id"} and _legacy_identity_materialized(before, after):
        # Some legacy brain rows expose only brain_record_id.  The repair
        # renderer materializes the canonical episode-scoped pair while
        # retaining that legacy ID, so the new pair is an identity alias rather
        # than fabricated research content.
        return isinstance(value, str) and value == _string(after.get("record_id"))
    if (
        field == "D_outcome"
        and value == {}
        and (before.get("D_outcome") is None or before.get("D_outcome") == "")
        and isinstance(before.get("payload"), dict)
        and before["payload"].get("D_outcome") is None
        and after.get("training_eligible") is False
        and _float(after.get("sample_weight")) == 0.0
        and (
            after.get("record_type")
            in {
                "supervised_issuer_day_case",
                "supervised_direct_event_case",
                "supervised_theme_formation_case",
            }
            or
            after.get("training_exclusion_reason")
            in {
                "NO_TRADABLE_ROW",
                "no_tradable_row",
                "NO_TRADABLE_ROW_ON_D",
                "no_tradable_row_on_d",
                "NO_VERIFIED_D_OUTCOME_ROW",
                "no_verified_D_outcome_row",
            }
            or before["payload"].get("response_class")
            in {"NO_TRADABLE_ROW", "no_tradable_row", "NO_TRADABLE_ROW_ON_D", "no_tradable_row_on_d"}
        )
    ):
        return True
    if field == "repair_population_derivations":
        return (
            isinstance(value, list)
            and bool(value)
            and all(
                isinstance(item, dict)
                and item.get("rule_id")
                in {
                    "case_id_from_unique_outcome_leader.v1",
                    "case_id_from_unique_evidence_join.v2",
                }
                for item in value
            )
        )
    if field == "provenance_source_filter":
        return _event_ticker_edge_source_filter_valid(before, after)
    if field in {target for _, target in _DERIVED_CASE_TARGETS.values()}:
        derivations = after.get("repair_population_derivations")
        return isinstance(derivations, list) and any(
            isinstance(item, dict)
            and item.get("rule_id")
            in {
                "case_id_from_unique_outcome_leader.v1",
                "case_id_from_unique_evidence_join.v2",
            }
            and item.get("target_field") == field
            and item.get("source_case_id") == value
            for item in derivations
        )
    if artifact_name == "source_ledger.jsonl" and field == "available_before_cutoff":
        return (
            (
                value is True
                and before.get("time_verified") is True
                and (
                    before.get("within_declared_window") is True
                    or before.get("used_in_blind") is True
                )
            )
            or (
                isinstance(value, bool)
                and verified_source_timestamp is not None
                and after.get("time_verified") is True
            )
        )
    if artifact_name == "source_ledger.jsonl" and field == "timestamp_repair_provenance":
        provenance_input_file = _string(value.get("input_file")) if isinstance(value, dict) else None
        declared_input_file = _string(
            before.get("input_file") or before.get("source_file")
        )
        declared_input_sha256 = before.get("input_sha256") or before.get(
            "source_sha256"
        )
        input_file_bound = provenance_input_file == declared_input_file or (
            declared_input_file is None
            and provenance_input_file is not None
            and Path(provenance_input_file).name == provenance_input_file
            and value.get("evidence_resolution") == "CONTENT_SHA256"
        )
        provenance_input_sha256 = _string(value.get("input_sha256"))
        input_sha256_bound = provenance_input_sha256 == declared_input_sha256 or (
            declared_input_sha256 is None
            and provenance_input_sha256 is not None
            and re.fullmatch(r"[0-9a-f]{64}", provenance_input_sha256) is not None
            and value.get("evidence_resolution") == "CONTENT_SHA256"
        )
        return (
            isinstance(value, dict)
            and value.get("rule_id") == NEWS_TIMESTAMP_REPAIR_RULE
            and value.get("published_at")
            == _first(after, "published_at_kst", "published_at")
            and value.get("published_at") == verified_source_timestamp
            and input_file_bound
            and input_sha256_bound
            and isinstance(value.get("content_sha256"), str)
            and isinstance(value.get("evidence_file"), str)
            and value.get("evidence_resolution")
            in {"DECLARED_FILENAME", "CONTENT_SHA256"}
        )
    if artifact_name == "final_semantic_audit.jsonl":
        if field == "company_name":
            return value in _recursive_values_for_keys(
                before,
                ("company_name", "company", "name"),
            )
        if field in {"status", "semantic_audit_status", "semantic_verdict"}:
            return value == "PASS" and _source_semantic_row_passes(before)
    if _string(after.get("record_type")) == "event_ticker_edge":
        has_source = bool(_string_list(after.get("provenance_source_ids")))
        if field in {
            "available_before_cutoff",
            "source_time_verified",
            "time_verified",
        }:
            return value is True and has_source
        if field == "edge_origin":
            return value == "BLIND_SOURCE_LEDGER" and has_source
        if field == "source_kind":
            return _string(value) is not None and has_source
    if field == "brain_delta_id":
        return value == after.get("record_id") and _is_namespaced_value(_record_id(before), value)
    if field == "record_id":
        return value == after.get("brain_delta_id") and _is_namespaced_value(
            _record_id(before),
            value,
        )
    if field == "legacy_source_episode_id":
        return (
            value == before.get("episode_id")
            and isinstance(after.get("episode_id"), str)
            and (_record_id(after) or "").startswith(f"{after['episode_id']}__")
        )
    if field == "legacy_mistyped_event_reference_values":
        original: list[str] = []
        retained: list[str] = []
        for event_field in _EVENT_REFERENCE_FIELDS:
            original.extend(_recursive_string_list_values_for_key(before, event_field))
            retained.extend(_recursive_string_list_values_for_key(after, event_field))
        original_direct = _string_list(before.get("direct_event_id"))
        original_singular = {
            item
            for singular_field in _SINGULAR_DOMAIN_EVENT_REFERENCE_FIELDS
            for item in _recursive_string_values_for_key(before, singular_field)
        }
        expected = (
            (set(original) - set(retained))
            | set(original_direct)
            | original_singular
        )
        return sorted(set(value)) == sorted(expected) if isinstance(value, list) else False
    if field == "repair_removed_null_event_reference_fields":
        expected_null_fields = sorted(
            event_field
            for event_field in _EVENT_REFERENCE_FIELDS
            if isinstance(before.get(event_field), list)
            and any(item is None for item in before[event_field])
            and (
                after.get(event_field)
                == [item for item in before[event_field] if item is not None]
                or (
                    event_field not in after
                    and not [item for item in before[event_field] if item is not None]
                )
            )
        )
        return bool(expected_null_fields) and sorted(_string_list(value)) == expected_null_fields
    if field in {"related_domain_ids", "missed_more_relevant_domain_ids"}:
        moved = _string_list(after.get("legacy_mistyped_event_reference_values"))
        return isinstance(value, list) and all(item in value for item in moved)
    if field == "selected_blind_screening_ids":
        moved = _string_list(after.get("legacy_mistyped_event_reference_values"))
        return (
            isinstance(value, list)
            and bool(value)
            and all(item in moved for item in value)
            and all(isinstance(item, str) and bool(item) for item in value)
        )
    if field == "source_ids":
        return bool(value == after.get("provenance_source_ids"))
    if field in {"provenance_source_ids", "source_fact_ids", "source_inference_ids"}:
        return isinstance(value, list) and bool(value == after.get(field))
    if field == "fact_ids":
        return bool(value == after.get("source_fact_ids"))
    if field == "inference_ids":
        return bool(value == after.get("source_inference_ids"))
    if field == "legacy_record_type":
        return bool(value == before.get("record_type"))
    if field == "training_exclusion_reason":
        return (
            after.get("training_eligible") is False
            and _float(after.get("sample_weight")) == 0.0
            and _string(value) is not None
        )
    if field == "training_eligible":
        return (
            before.get("training_eligible") in {None, ""}
            and value is False
            and _float(after.get("sample_weight")) == 0.0
            and _string(after.get("training_exclusion_reason")) is not None
        )
    if field == "semantic_exclusion_relation_ids":
        return (
            before.get("training_eligible") is True
            and after.get("training_eligible") is False
            and _float(after.get("sample_weight")) == 0.0
            and after.get("training_exclusion_reason") == "semantic_contract_failed"
            and isinstance(value, list)
            and bool(value)
            and all(isinstance(item, str) and bool(item) for item in value)
        )
    if field == "eligibility_reason":
        return (
            after.get("training_eligible") is False
            and _float(after.get("sample_weight")) == 0.0
            and value == after.get("training_exclusion_reason")
        )
    if field == "training_target":
        return value in _KNOWN_TRAINING_TARGETS
    if field == "known_at" and after.get("record_type") == "company_memory_delta":
        available_from = after.get("available_from")
        return bool(
            value == available_from
            or (
                isinstance(available_from, str)
                and re.fullmatch(r"\d{4}-\d{2}-\d{2}", available_from)
                and value == f"{available_from}T00:00:00+09:00"
            )
        )
    if field == "available_from":
        return isinstance(value, str) and value == after.get("available_from")
    if field == "direct_event_fact_id":
        return (
            value == before.get("direct_event_id")
            and isinstance(value, str)
            and value in _string_list(after.get("legacy_mistyped_event_reference_values"))
        )
    if field == "sample_weight":
        if (
            before.get("training_eligible") is True
            and after.get("training_eligible") is True
            and before.get("sample_weight") in {None, ""}
            and _float(value) == 1.0
        ):
            return True
        return (
            after.get("training_eligible") is False
            and _float(value) == 0.0
            and _string(after.get("training_exclusion_reason")) is not None
        ) or (
            after.get("issuer_day_sample_weight_policy") == "fractional_issuer_day_group" and 0.0 < _float(value) <= 1.0
        )
    if field == "issuer_day_sample_weight_policy":
        return value in {"fractional_issuer_day_group", "single_issuer_day_case"} and bool(
            _string(after.get("issuer_day_weight_group_id")) or _string(after.get("issuer_day_case_id"))
        )
    if field == "issuer_day_weight_group_id":
        trade_date = _string(after.get("trade_date"))
        ticker = _string(after.get("ticker"))
        case_id = _string(after.get("issuer_day_case_id"))
        return value in {
            candidate
            for candidate in (
                f"{trade_date}:{ticker}" if trade_date and ticker else None,
                case_id,
            )
            if candidate is not None
        }
    if field in {"company_name", "ticker"}:
        if field == "ticker":
            related_tickers = list(
                dict.fromkeys(
                    [
                        *_string_list(before.get("related_tickers")),
                        *(
                            _string_list(before["payload"].get("related_tickers"))
                            if isinstance(before.get("payload"), dict)
                            else []
                        ),
                    ]
                )
            )
            if len(related_tickers) == 1 and value == related_tickers[0]:
                return True
        aliases = (
            (field, "company", "name", "issuer_name")
            if field == "company_name"
            else (field, "code")
        )
        return value in _recursive_values_for_keys(before, aliases)
    if field == "audit_id":
        return bool(value == after.get("record_id"))
    if field == "outcome_high_return_pct":
        return value in _recursive_values_for_keys(
            before,
            ("outcome_high_return_pct", "high_return_pct", "D_high_return_pct"),
        )
    if field == "path_type":
        return bool(value == _expected_path_type(before))
    if field == "relation_class":
        return bool(
            before.get("record_type") == "event_ticker_edge"
            and value == _expected_path_type(before)
        )
    return False


def _source_semantic_row_passes(row: dict[str, Any]) -> bool:
    verdict = _string(
        row.get("semantic_verdict")
        or row.get("semantic_audit_status")
        or row.get("status")
        or row.get("audit_decision")
        or row.get("audit_status")
        or row.get("semantic_gate_status")
        or row.get("semantic_entailment")
        or row.get("semantic_result")
        or row.get("audit_result")
        or row.get("verdict")
    )
    verdict_upper = (verdict or "").upper()
    inferred_pass = (
        row.get("chain_complete") is True
        and row.get("quote_found_in_source_row") is True
        and not _string_list(row.get("fail_reasons"))
    )
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
        and _string(row.get("final_evidence_witness_id") or row.get("witness_id")) is not None
        and not _string_list(row.get("fail_reasons"))
    )
    return verdict_upper in {"PASS", "PASSED"} or inferred_pass or explicit_boolean_pass or corroborated_legacy_pass


def _recursive_values_for_keys(value: Any, keys: tuple[str, ...]) -> set[Any]:
    values: set[Any] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in keys and isinstance(nested, (str, int, float, bool)):
                values.add(nested)
            values.update(_recursive_values_for_keys(nested, keys))
    elif isinstance(value, list):
        for nested in value:
            values.update(_recursive_values_for_keys(nested, keys))
    return values


def _recursive_string_list_values_for_key(value: Any, key: str) -> list[str]:
    """Collect list-valued reference fields without flattening other fields."""

    values: list[str] = []
    if isinstance(value, dict):
        for name, nested in value.items():
            if name == key:
                values.extend(_string_list(nested))
            values.extend(_recursive_string_list_values_for_key(nested, key))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_recursive_string_list_values_for_key(nested, key))
    return values


def _recursive_string_values_for_key(value: Any, key: str) -> list[str]:
    values: list[str] = []
    if isinstance(value, dict):
        for name, nested in value.items():
            if name == key and isinstance(nested, str) and nested:
                values.append(nested)
            values.extend(_recursive_string_values_for_key(nested, key))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_recursive_string_values_for_key(nested, key))
    return values


def _legacy_source_has_outcome(source: dict[str, Any]) -> bool:
    outcome_fields = {
        "D_high_return_pct",
        "D_close_return_pct",
        "high_return_pct",
        "close_return_pct",
        "upper_limit_touched",
        "response_label",
    }
    payload = source.get("payload")
    return any(
        isinstance(container, dict)
        and (
            any(field in container for field in outcome_fields)
            or any(
                isinstance(container.get(field), dict) and bool(container[field])
                for field in ("D_outcome", "outcome", "label")
            )
        )
        for container in (source, payload)
    )


def _legacy_verified_outcome_matches_source(
    value: Any,
    source: dict[str, Any],
) -> bool:
    """Accept a verified outcome mirror only when every value exists in source."""

    if not isinstance(value, dict) or not value or not _legacy_source_has_outcome(source):
        return False
    substantive = False
    aliases = {
        "high_return_pct": ("high_return_pct", "D_high_return_pct"),
        "close_return_pct": ("close_return_pct", "D_close_return_pct"),
    }
    for key, item in value.items():
        if key == "label_quality":
            if item != "verified":
                return False
            continue
        substantive = True
        source_values = [
            nested
            for alias in aliases.get(key, (key,))
            for nested in _recursive_values_for_key(source, alias)
        ]
        if not any(_relation_scalar_equal(candidate, item) for candidate in source_values):
            return False
    return substantive


def _recursive_values_for_key(value: Any, key: str) -> list[Any]:
    values: list[Any] = []
    if isinstance(value, dict):
        for name, nested in value.items():
            if name == key:
                values.append(nested)
            values.extend(_recursive_values_for_key(nested, key))
    elif isinstance(value, list):
        for nested in value:
            values.extend(_recursive_values_for_key(nested, key))
    return values


def _derived_scalar_values_are_source_anchored(value: Any, source: Any) -> bool:
    """Allow canonical feature mirrors only when every scalar came from source."""

    if not isinstance(value, dict):
        return False
    source_values = _all_scalar_values(source)
    derived_values = _all_scalar_values(value)
    return bool(derived_values) and derived_values <= source_values


def _all_scalar_values(value: Any) -> set[str | int | float | bool]:
    if isinstance(value, dict):
        result: set[str | int | float | bool] = set()
        for nested in value.values():
            result.update(_all_scalar_values(nested))
        return result
    if isinstance(value, list):
        result = set()
        for nested in value:
            result.update(_all_scalar_values(nested))
        return result
    if isinstance(value, (str, int, float, bool)):
        return {value}
    return set()


def _expected_path_type(record: dict[str, Any]) -> str:
    payload = record.get("payload")
    payload_dict = payload if isinstance(payload, dict) else {}
    existing = _string(
        record.get("path_type")
        or payload_dict.get("path_type")
        or record.get("candidate_path_type")
        or payload_dict.get("candidate_path_type")
    )
    allowed = {"DIRECT", "CONTINUATION", "FUNDAMENTAL", "INFERRED_NEW", "MARKET_MEMORY"}
    if existing is not None and existing.upper() in allowed:
        return existing.upper()
    edge = _string(
        record.get("edge_type")
        or payload_dict.get("edge_type")
        or record.get("relation_class")
        or payload_dict.get("relation_class")
        or record.get("catalyst_type")
        or payload_dict.get("catalyst_type")
    )
    normalized = (edge or "").upper()
    if "DIRECT" in normalized:
        return "DIRECT"
    if "CONTINUATION" in normalized:
        return "CONTINUATION"
    if "FUNDAMENTAL" in normalized:
        return "FUNDAMENTAL"
    if "MEMORY" in normalized:
        return "MARKET_MEMORY"
    return "INFERRED_NEW"


def _is_record_identity_field(field: str) -> bool:
    return field in {
        "record_id",
        "brain_delta_id",
        "brain_record_id",
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
    } or field.endswith("_record_id")


def _known_record_type_transition(old: Any, new: Any) -> bool:
    if not isinstance(old, str) or not isinstance(new, str):
        return False
    normalized = old.strip().lower()
    if normalized == new:
        return True
    issuer_aliases = {
        "final_candidate_outcome",
        "forecast_scorecard_record",
        "forecast_selection_result",
        "issuer_day",
        "issuer_day_case",
        "issuer_day_candidate_outcome",
        "issuer_day_final_prediction_outcome",
        "issuer_day_final_watchlist_supervision",
        "issuer_day_outcome",
        "issuer_day_outcome_case",
        "issuer_day_prediction_outcome",
        "issuer_day_supervised",
        "issuer_day_supervised_record",
        "issuer_day_weight_update",
        "supervised_final_watchlist_case",
    }
    direct_aliases = {
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
    }
    negative_aliases = {
        "blind_false_positive",
        "cutline_exclusion_outcome",
        "negative_control",
        "negative_control_final_false_positive",
        "negative_control_final_miss",
        "negative_control_no_issuer_record",
        "negative_control_source_case",
        "nonfinal_rankable_pairwise_case",
        "prediction_error_false_positive",
        "selected_negative_control_source",
        "semantic_guard_case",
        "supervised_rankable_not_final_case",
    }
    if normalized in issuer_aliases:
        return new == "supervised_issuer_day_case"
    if normalized in direct_aliases:
        return new == "supervised_direct_event_case"
    if normalized in negative_aliases:
        return new == "negative_control_case"
    if normalized in {"blind_leader_pair_case", "counterfactual_pair"}:
        return new == "blind_leader_preference_pair"
    pairwise_aliases = {
        "pairwise_correction",
        "pairwise_rank_delta",
        "pairwise_rank_error",
    }
    newsless_aliases = {
        "newsless_leader_control",
        "newsless_outcome_case",
        "newsless_outcome_leader_case",
    }
    theme_aliases = {"theme_case", "theme_outcome_case"}
    if normalized in pairwise_aliases:
        return new == "ranking_error_case"
    if normalized in newsless_aliases:
        return new == "newsless_or_unexplained_case"
    if normalized in theme_aliases:
        return new == "theme_formation_case"
    outcome_leader_aliases = {
        *_OUTCOME_LEADER_MISS_ALIASES,
        "outcome_leader_case",
        "outcome_leader_reverse_audit",
        "outcome_leader_reverse_audit_case",
        "outcome_leader_reverse_audit_record",
    }
    if normalized in outcome_leader_aliases:
        return new in {
            "candidate_generation_error_case",
            "context_market_state_or_fact_case",
            "newsless_or_unexplained_case",
            "ranking_error_case",
        }
    return False


def _is_namespaced_value(old: Any, new: Any) -> bool:
    if not isinstance(old, str) or not isinstance(new, str):
        return False
    if new == old or new.endswith(f"__{old}"):
        return True
    if "__" not in old or "__" not in new:
        return False
    old_prefix, old_suffix = old.rsplit("__", 1)
    new_prefix, new_suffix = new.rsplit("__", 1)
    return old_prefix.casefold() == new_prefix.casefold() and old_suffix == new_suffix


def _value_or_namespaced_value_present(value: Any, candidates: list[Any]) -> bool:
    return value in candidates or any(_is_namespaced_value(value, candidate) for candidate in candidates)


def _case_identifier_fields() -> tuple[str, ...]:
    return tuple(dict.fromkeys(field for fields in _CASE_ID_ALIASES.values() for field in fields))


def _derived_origin_index(
    source_rows: list[ArtifactRow],
) -> dict[tuple[str, str], ArtifactRow]:
    index: dict[tuple[str, str], ArtifactRow] = {}
    for block_name, (category, identifier_field) in _CASE_BLOCKS.items():
        record_type = _CASE_RECORD_TYPES[category]
        for row in source_rows:
            if row.canonical_name != block_name:
                continue
            identifier = _first(
                row.row,
                *_CASE_ID_ALIASES.get(block_name, (identifier_field,)),
            )
            if identifier is not None:
                index[(record_type, identifier)] = row
    return index


def _derived_origin_for_record(
    record: dict[str, Any],
    origins: dict[tuple[str, str], ArtifactRow],
) -> ArtifactRow | None:
    record_type = _string(record.get("record_type"))
    if record_type is None:
        return None
    for block_name, (category, identifier_field) in _CASE_BLOCKS.items():
        if _CASE_RECORD_TYPES[category] != record_type:
            continue
        for field in _CASE_ID_ALIASES.get(block_name, (identifier_field,)):
            for identifier in _canonical_case_ids(record, field):
                origin = origins.get((record_type, identifier))
                if origin is not None:
                    return origin
    return None


def _transform_rules(
    before: dict[str, Any],
    after: dict[str, Any] | None,
) -> list[str]:
    if after is None:
        return []
    rules: list[str] = []
    if _record_id(before) != _record_id(after):
        rules.append("ID_NAMESPACED")
    if before.get("record_type") != after.get("record_type"):
        rules.append("TYPE_CANONICALIZED")
    if before.get("training_eligible") != after.get("training_eligible"):
        rules.append("ELIGIBILITY_CHANGED")
    if _changed_fields(before, after):
        rules.append("FIELD_NORMALIZED")
    return rules


def _bundle_cutoff(by_name: dict[str, list[ArtifactRow]]) -> datetime | None:
    for block_name in (
        "blind_prediction.json",
        "research_episode.json",
        "phase_state.json",
    ):
        for artifact_row in by_name.get(block_name, []):
            row = artifact_row.row
            cutoff = _parse_datetime_or_none(row.get("cutoff_at") or row.get("cutoff_kst") or row.get("cutoff"))
            if cutoff is not None:
                return cutoff
            if block_name == "research_episode.json":
                coverage = row.get("coverage")
                if isinstance(coverage, dict):
                    cutoff = _parse_datetime_or_none(
                        coverage.get("expected_end")
                    )
                    if cutoff is not None:
                        return cutoff
    return None


def _bundle_trade_date(by_name: dict[str, list[ArtifactRow]]) -> datetime | None:
    for block_name in (
        "blind_prediction.json",
        "research_episode.json",
        "phase_state.json",
    ):
        for artifact_row in by_name.get(block_name, []):
            trade_date = _parse_datetime_or_none(artifact_row.row.get("trade_date"))
            if trade_date is not None:
                return trade_date
    return None


def _parse_datetime_or_none(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.strip()
    # Legacy CSV source ledgers use an explicit timezone abbreviation instead
    # of an ISO offset (for example ``2018-05-02 08:58:54 KST``).  This is a
    # representation alias, not a guessed timestamp: retain the instant and
    # make the offset explicit before handing it to the shared parser.
    if normalized.upper().endswith(" KST"):
        normalized = f"{normalized[:-4].rstrip()}+09:00"
    elif normalized.upper().endswith(" UTC"):
        normalized = f"{normalized[:-4].rstrip()}+00:00"
    try:
        return parse_datetime(normalized)
    except ValueError:
        return None


def _has_explicit_timezone(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().replace("Z", "+00:00").replace("z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).tzinfo is not None
    except ValueError:
        return False


def _bundle_prior_context_verified(
    by_name: dict[str, list[ArtifactRow]],
    *,
    cutoff: datetime | None,
) -> bool:
    if cutoff is None:
        return False
    previous_dates: set[str] = set()
    for block_name in (
        "source_ledger.jsonl",
        "research_episode.json",
        "phase_state.json",
        "blind_prediction.json",
    ):
        for artifact_row in by_name.get(block_name, []):
            raw_date = _first(
                artifact_row.row,
                "previous_trade_date",
                "previous_market_trade_date",
                "p_snapshot_date",
                "blind_snapshot_date",
            )
            parsed = _parse_datetime_or_none(raw_date)
            if parsed is not None and parsed.date() < cutoff.date():
                previous_dates.add(parsed.date().isoformat())
    if not previous_dates:
        return False
    for artifact_row in by_name.get("source_ledger.jsonl", []):
        row = artifact_row.row
        role = str(row.get("logical_role") or row.get("source_role") or "").upper()
        usage_phase = str(row.get("usage_phase") or "").upper()
        path = str(row.get("path") or row.get("source_path") or "")
        source_type = str(row.get("source_type") or "").upper()
        snapshot_hash = row.get("sha256") or row.get("content_sha256")
        snapshot_path = " ".join(
            str(value or "")
            for value in (path, row.get("url"), row.get("title"), row.get("notes"))
        )
        legacy_snapshot_source = (
            "BLIND" in source_type
            and "SNAPSHOT" in source_type
            and row.get("available_before_cutoff") is True
            and row.get("time_verified") is True
            and _is_sha256(snapshot_hash)
            and any(day in snapshot_path or day.replace("-", "") in snapshot_path for day in previous_dates)
        )
        if legacy_snapshot_source:
            return True
        if (
            "PREVIOUS_TRADE_DATE" not in role
            or "SNAPSHOT" not in role
            or "BLIND" not in usage_phase
            or row.get("cutoff_safe") is not True
            or not _is_sha256(row.get("sha256"))
        ):
            continue
        if any(day.replace("-", "") in path or day in path for day in previous_dates):
            return True
    if _verified_legacy_prior_snapshot_access(
        by_name,
        previous_dates=previous_dates,
    ):
        return True
    return _sealed_manifest_prior_context_verified(
        by_name,
        previous_dates=previous_dates,
    )


def _verified_legacy_prior_snapshot_access(
    by_name: dict[str, list[ArtifactRow]],
    *,
    previous_dates: set[str],
) -> bool:
    """Bind an older P-snapshot date to a verified, hashed BLIND access row."""

    if not previous_dates:
        return False
    final_context_dates = {
        parsed.date().isoformat()
        for prediction in by_name.get("blind_prediction.json", [])
        for final_watchlist in [prediction.row.get("final_watchlist")]
        if isinstance(final_watchlist, list)
        for candidate in final_watchlist
        if isinstance(candidate, dict)
        for context in [
            next(
                (
                    candidate.get(field)
                    for field in (
                        "p_snapshot_context",
                        "P_snapshot_context",
                        "safe_D1_context",
                        "safe_d1_context",
                    )
                    if isinstance(candidate.get(field), dict)
                ),
                None,
            )
        ]
        if isinstance(context, dict)
        for raw_date in [
            _first(
                context,
                "snapshot_date",
                "as_of_date",
                "trade_date",
                "p_snapshot_date",
            )
        ]
        for parsed in [_parse_datetime_or_none(raw_date)]
        if parsed is not None
    }
    if final_context_dates and not final_context_dates.issubset(previous_dates):
        return False
    for artifact_row in by_name.get("access_log.jsonl", []):
        row = artifact_row.row
        resource = _access_resource(row)
        action = _access_action(row)
        combined = f"{resource} {action}"
        status = str(row.get("result") or row.get("status") or "").upper()
        phase = str(row.get("phase") or row.get("usage_phase") or "").upper()
        action_verified = any(
            token in action
            for token in ("FRESH_DOWNLOAD_VERIFY", "VERIFY_BLIND", "REOPEN_VERIFIED")
        )
        status_verified_prior = "PRESEAL_SAFE_P_SNAPSHOT" in status
        phase_verified_prior = (
            ("P_SNAPSHOT" in combined or status_verified_prior)
            and "OUTCOME" not in combined
        )
        if (
            ("P_SNAPSHOT" not in combined and "BLIND_SNAPSHOT" not in combined)
            or "OUTCOME" in combined
            or ("BLIND" not in phase and not phase_verified_prior)
            or not (
                any(token in status for token in ("VERIFIED", "PASS", "SUCCESS"))
                or action_verified
            )
            or any(token in status for token in ("FAIL", "ERROR", "INVALID"))
            or not _is_sha256(row.get("sha256"))
        ):
            continue
        byte_size = row.get("byte_size")
        row_count = row.get("row_count")
        if (
            (
                not isinstance(byte_size, int)
                or isinstance(byte_size, bool)
                or byte_size <= 0
            )
            and not action_verified
            and not status_verified_prior
        ):
            continue
        if (
            not isinstance(row_count, int)
            or isinstance(row_count, bool)
            or row_count <= 0
        ) and not (status_verified_prior and isinstance(byte_size, int) and byte_size > 0):
            continue
        return True
    return False


def _sealed_manifest_prior_context_verified(
    by_name: dict[str, list[ArtifactRow]],
    *,
    previous_dates: set[str],
) -> bool:
    manifests = by_name.get("blind_packet_manifest.json", [])
    receipts = by_name.get("blind_seal_receipt.json", [])
    if len(manifests) != 1 or len(receipts) != 1:
        return False
    manifest = manifests[0].row
    receipt = receipts[0].row
    snapshot_path = _string(manifest.get("blind_snapshot_file") or manifest.get("blind_snapshot_path"))
    snapshot_sha256 = _string(manifest.get("blind_snapshot_sha256"))
    manifest_outcome_untouched = (
        manifest.get("outcome_file_bytes_accessed") is False or manifest.get("outcome_snapshot_not_downloaded") is True
    )
    if (
        snapshot_path is None
        or not _is_sha256(snapshot_sha256)
        or not any(day in snapshot_path or day.replace("-", "") in snapshot_path for day in previous_dates)
        or not manifest_outcome_untouched
    ):
        return False
    manifest_counters = manifest.get("preseal_counters")
    structured_manifest_counters_zero = (
        isinstance(manifest_counters, dict)
        and bool(manifest_counters)
        and all(_int(value, default=-1) == 0 for value in manifest_counters.values())
    )
    legacy_manifest_counter_zero = (
        _int(
            manifest.get("preseal_outcome_access_count"),
            default=-1,
        )
        == 0
    )
    if not (structured_manifest_counters_zero or legacy_manifest_counter_zero):
        return False
    manifest_sha256 = _string(receipt.get("blind_packet_manifest_sha256"))
    if not _is_sha256(manifest_sha256):
        return False
    receipt_order_verified = receipt.get("receipt_written_before_any_outcome_access") is True or (
        receipt.get("seal_verified") is True
        and receipt.get("outcome_access_allowed_after_this_receipt") is True
        and _verified_seal_precedes_outcome_access(by_name)
    )
    if not receipt_order_verified:
        return False
    sealed_artifacts = receipt.get("sealed_artifacts")
    sealed_manifest_link = isinstance(sealed_artifacts, list) and any(
        isinstance(artifact, dict)
        and artifact.get("name") == "blind_packet_manifest.json"
        and artifact.get("sha256") == manifest_sha256
        for artifact in sealed_artifacts
    )
    legacy_snapshot_link = (
        receipt.get("seal_verified") is True
        and receipt.get("blind_snapshot_sha256") == snapshot_sha256
        and _verified_seal_precedes_outcome_access(by_name)
    )
    if not (sealed_manifest_link or legacy_snapshot_link):
        return False
    receipt_counters = {
        field: value
        for field, value in receipt.items()
        if field.startswith("preseal_outcome_") and field.endswith("_count")
    }
    return bool(receipt_counters) and all(_int(value, default=-1) == 0 for value in receipt_counters.values())


def _verified_seal_precedes_outcome_access(
    by_name: dict[str, list[ArtifactRow]],
) -> bool:
    ordered_rows = sorted(
        (
            (stream_order, _access_sequence(row), row)
            for stream_order, block_name in enumerate(("access_log.jsonl", "postseal_access_log.jsonl"))
            for row in by_name.get(block_name, [])
        ),
        key=lambda item: (item[0], item[1]),
    )
    seal_positions = [
        (stream_order, sequence) for stream_order, sequence, row in ordered_rows if _is_verified_seal_row(row.row)
    ]
    actual_outcome_positions = [
        (stream_order, sequence) for stream_order, sequence, row in ordered_rows if _is_actual_outcome_access(row.row)
    ]
    if not seal_positions:
        return False
    if not actual_outcome_positions:
        return True
    first_outcome = min(actual_outcome_positions)
    return any(position < first_outcome for position in seal_positions)


def _contains_outcome_only_payload(
    value: Any,
    *,
    cutoff: datetime | None = None,
    prior_context_verified: bool = False,
) -> bool:
    forbidden = {
        "D_outcome",
        "D_response",
        "close_return_pct",
        "outcome_leader_id",
        "outcome_response_class",
        "postmortem_label",
        "response_class",
    }
    if isinstance(value, dict):
        verified_prior_context_present = prior_context_verified or _has_verified_prior_context(
            value,
            cutoff=cutoff,
        )
        for key, nested in value.items():
            if key in _SAFE_PRIOR_CONTEXT_FIELDS:
                if nested is None or nested is False or nested == 0:
                    continue
                if _is_verified_prior_context(nested, cutoff=cutoff):
                    continue
                if verified_prior_context_present and not _contains_strong_outcome_marker(nested):
                    continue
                return True
            if key in forbidden or _contains_outcome_only_payload(
                nested,
                cutoff=cutoff,
                prior_context_verified=verified_prior_context_present,
            ):
                return True
        return False
    if isinstance(value, list):
        return any(
            _contains_outcome_only_payload(
                nested,
                cutoff=cutoff,
                prior_context_verified=prior_context_verified,
            )
            for nested in value
        )
    return False


def _contains_strong_outcome_marker(value: Any) -> bool:
    strong_markers = {
        "D_outcome",
        "D_response",
        "outcome_leader_id",
        "outcome_response_class",
        "postmortem_label",
        "response_class",
    }
    if isinstance(value, dict):
        return any(key in strong_markers or _contains_strong_outcome_marker(nested) for key, nested in value.items())
    if isinstance(value, list):
        return any(_contains_strong_outcome_marker(item) for item in value)
    return False


def _is_verified_prior_context(value: Any, *, cutoff: datetime | None) -> bool:
    if not isinstance(value, dict) or cutoff is None:
        return False
    raw_date = _first(
        value,
        "snapshot_date",
        "as_of_date",
        "trade_date",
        "p_snapshot_date",
        "previous_trade_date",
    )
    if raw_date is None:
        return False
    parsed = _parse_datetime_or_none(raw_date)
    return parsed is not None and parsed.date() < cutoff.date()


def _has_verified_prior_context(value: Any, *, cutoff: datetime | None) -> bool:
    if isinstance(value, dict):
        return any(
            (key in _SAFE_PRIOR_CONTEXT_FIELDS and _is_verified_prior_context(nested, cutoff=cutoff))
            or _has_verified_prior_context(nested, cutoff=cutoff)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_has_verified_prior_context(nested, cutoff=cutoff) for nested in value)
    return False


_RANKING_PRIOR_METRIC_ALIASES = {
    "safe_p_amount_rank": "amount_rank",
    "safe_p_turnover_rank": "turnover_rank",
    "safe_p_high_return_pct": "high_return_pct",
    "safe_p_close_return_pct": "close_return_pct",
    "safe_p_return_5d_pct": "return_5d_pct",
    "safe_p_return_20d_pct": "return_20d_pct",
}


def _ranking_row_has_verified_prior_context(
    row: dict[str, Any],
    *,
    screenings_by_id: dict[str, dict[str, Any]],
    cutoff: datetime | None,
) -> bool:
    if row.get("safe_D1_context_used") is not True:
        return False
    screening_id = _first(row, "source_screening_id", "screening_id")
    if screening_id is None:
        return False
    screening = screenings_by_id.get(screening_id)
    if screening is None:
        return False
    row_candidate_id = _first(row, "candidate_id")
    screening_candidate_id = _first(screening, "candidate_id")
    if row_candidate_id is not None and row_candidate_id != screening_candidate_id:
        return False
    row_ticker = _first(row, "ticker", "code")
    screening_ticker = _first(screening, "ticker", "code")
    if row_ticker is not None and row_ticker != screening_ticker:
        return False

    context = next(
        (
            screening.get(field)
            for field in ("safe_D1_context", "safe_d1_context", "P_snapshot", "p_snapshot")
            if isinstance(screening.get(field), dict)
        ),
        None,
    )
    if not isinstance(context, dict) or not _is_verified_prior_context(context, cutoff=cutoff):
        return False
    ranking_inputs = row.get("ranking_inputs")
    if not isinstance(ranking_inputs, dict):
        return False
    matched_metric_count = 0
    for input_field, context_field in _RANKING_PRIOR_METRIC_ALIASES.items():
        if input_field not in ranking_inputs:
            continue
        if context_field not in context or not _relation_scalar_equal(
            ranking_inputs[input_field],
            context[context_field],
        ):
            return False
        matched_metric_count += 1
    return matched_metric_count > 0


def _relation_scalar_equal(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if left is right or left == right:
        return True
    # JSON-compatible legacy bundles sometimes carry an unquoted NaN metric.
    # Python/Decimal normally treats NaN as unequal to itself, but an unchanged
    # NaN on both sides is a preserved value, not an illegal repair.
    if (
        isinstance(left, float)
        and isinstance(right, float)
        and math.isnan(left)
        and math.isnan(right)
    ):
        return True
    try:
        left_decimal = Decimal(str(left).strip())
        right_decimal = Decimal(str(right).strip())
        if left_decimal.is_nan() and right_decimal.is_nan():
            return True
        return left_decimal == right_decimal
    except (InvalidOperation, ValueError):
        return isinstance(left, str) and isinstance(right, str) and left.strip() == right.strip()


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _bool_or_none(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _int(value: Any, *, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _float(value: Any) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return 0.0
    return 0.0
