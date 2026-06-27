# Brain Record Store Report

- schema_version: `nslab.brain_record_store_report.v1`
- record_count: `169`
- raw_record_count: `169`
- normalized_record_count: `169`
- raw_normalized_record_count_matches: `True`
## raw_record_counts_by_episode

- EP-2991eb145f59: `1`
- EP-f56c790fe2fc: `1`
- NSLAB-20241204-06A72B21: `165`
- NSLAB-20300110-SYNTH: `2`

- all_record_count: `169`
- staged_record_count: `0`
- training_eligible_record_count: `144`
## record_counts_by_type

- blind_leader_preference_pair: `21`
- candidate_generation_error_case: `8`
- context_market_state_or_fact_case: `17`
- memory_claim: `2`
- negative_control_case: `13`
- newsless_or_unexplained_case: `43`
- ranking_error_case: `1`
- supervised_direct_event_case: `28`
- supervised_issuer_day_case: `29`
- theme_formation_case: `7`

## record_counts_by_evidence_phase

- AUDIT: `2`
- POSTMORTEM: `167`

## record_counts_by_training_target

- candidate_generation_correction: `8`
- candidate_ranking_correction: `1`
- context_market_state_or_fact: `17`
- direct_event_response: `28`
- issuer_day_price_response: `29`
- legacy_catalog_only: `2`
- negative_control_calibration: `13`
- newsless_outcome_calibration: `43`
- outcome_preferred_candidate: `21`
- theme_formation_response: `7`

## record_counts_by_typed_payload_status

- KNOWN_TYPED_PAYLOAD: `169`

- unknown_typed_payload_count: `0`
- raw_only_record_count: `0`
- ineligible_record_count: `25`
- all_unknown_typed_payload_count: `0`
- all_raw_only_record_count: `0`
- staged_unknown_typed_payload_count: `0`
- staged_raw_only_record_count: `0`
## unknown_typed_payload_record_ids

- none

## raw_only_record_ids

- none

## brain_delta_duplicate_record_ids

- none

## all_unknown_typed_payload_record_ids

- none

## all_raw_only_record_ids

- none

## staged_unknown_typed_payload_record_ids

- none

## staged_raw_only_record_ids

- none

## warehouse_counts

- beneficiary_cases: `0`
- brain_records: `169`
- company_memory: `5`
- company_memory_delta_records: `0`
- company_memory_delta_written: `0`
- daily_outcomes: `1`
- direct_event_cases: `28`
- error_cases: `9`
- event_sources: `0`
- event_ticker_edges: `0`
- events: `0`
- issuer_day_cases: `29`
- leader_pairs: `21`
- market_memory: `5`
- mechanism_memory: `6`
- memory_claims: `2`
- predictions: `1`
- record_coverage: `13`
- record_provenance: `183`
- research_episodes: `2`
- research_questions: `0`
- theme_formation_cases: `7`

- dropped_record_count: `0`
- extra_normalized_record_count: `0`
- quarantined_bundle_count: `0`
- quarantined_raw_record_count: `0`
- quarantined_normalized_record_count: `0`
- quarantined_record_count: `0`
## quarantine_reasons


## quarantine_normalization_skipped_reasons


- audit_passed: `True`
## record_store_audit

- schema_version: `nslab.record_store_audit.v1`
- passed: `True`
- deep: `True`
- record_count: `169`
- all_record_count: `169`
- staged_record_count: `0`
- episode_count: `4`
- training_eligible_record_count: `144`
- duplicate_record_ids: `[]`
- unknown_training_enabled_record_ids: `[]`
- unknown_typed_payload_record_ids: `[]`
- raw_only_record_ids: `[]`
- all_unknown_typed_payload_record_ids: `[]`
- all_raw_only_record_ids: `[]`
- staged_unknown_typed_payload_record_ids: `[]`
- staged_raw_only_record_ids: `[]`
- payload_hash_mismatch_record_ids: `[]`
- eligible_records_without_provenance: `[]`
- invalid_outcome_label_quality_record_ids: `[]`
- missing_record_manifest_episode_ids: `[]`
- manifest_count_mismatch_episode_ids: `[]`
- manifest_record_id_mismatch_episode_ids: `[]`
- manifest_training_eligible_mismatch_episode_ids: `[]`
- manifest_type_count_mismatch_episode_ids: `[]`
- manifest_records_file_missing_episode_ids: `[]`
- manifest_records_file_absolute_episode_ids: `[]`
- manifest_records_file_escape_episode_ids: `[]`
- manifest_hash_mismatch_episode_ids: `[]`
- missing_normalized_index_episode_ids: `[]`
- index_record_id_mismatch_episode_ids: `[]`
- index_training_eligible_mismatch_episode_ids: `[]`
- index_type_count_mismatch_episode_ids: `[]`
- missing_bundle_envelope_episode_ids: `[]`
- raw_block_hash_mismatch_episode_ids: `[]`
- brain_delta_count_mismatch_episode_ids: `[]`
- brain_delta_record_id_mismatch_episode_ids: `[]`
- brain_delta_duplicate_record_ids: `[]`
- brain_delta_training_eligible_mismatch_episode_ids: `[]`
- brain_delta_type_count_mismatch_episode_ids: `[]`
- records_missing_source_block: `[]`
- records_missing_source_line: `[]`
- records_with_invalid_source_line: `[]`
- records_with_raw_payload_hash_mismatch: `[]`
- eligible_records_with_unknown_provenance_sources: `[]`
- source_ledger_source_id_mismatch_episode_ids: `[]`
- records_with_unknown_payload_references: `[]`
- missing_payload_references: `[]`
- records_with_naive_available_from: `[]`
- invalid_event_ticker_edge_path_type_record_ids: `[]`
- event_ticker_edge_cutoff_provenance_violation_record_ids: `[]`
- event_ticker_edge_source_ledger_cutoff_violation_record_ids: `[]`
- invalid_company_memory_delta_known_at_record_ids: `[]`
- backdated_company_memory_delta_known_at_record_ids: `[]`
- issuer_day_event_level_weight_mismatch_record_ids: `[]`
- findings: `[]`
- stats: `{'record_count': 169, 'episode_count': 4, 'training_eligible_record_count': 144, 'record_counts_by_type': {'blind_leader_preference_pair': 21, 'candidate_generation_error_case': 8, 'context_market_state_or_fact_case': 17, 'memory_claim': 2, 'negative_control_case': 13, 'newsless_or_unexplained_case': 43, 'ranking_error_case': 1, 'supervised_direct_event_case': 28, 'supervised_issuer_day_case': 29, 'theme_formation_case': 7}, 'record_counts_by_typed_payload_status': {'KNOWN_TYPED_PAYLOAD': 169}, 'record_counts_by_evidence_phase': {'AUDIT': 2, 'POSTMORTEM': 167}, 'record_counts_by_training_target': {'candidate_generation_correction': 8, 'candidate_ranking_correction': 1, 'context_market_state_or_fact': 17, 'direct_event_response': 28, 'issuer_day_price_response': 29, 'legacy_catalog_only': 2, 'negative_control_calibration': 13, 'newsless_outcome_calibration': 43, 'outcome_preferred_candidate': 21, 'theme_formation_response': 7}, 'unknown_typed_payload_count': 0, 'raw_only_record_count': 0, 'ineligible_record_count': 25}`
- all_stats: `{'record_count': 169, 'episode_count': 4, 'training_eligible_record_count': 144, 'record_counts_by_type': {'blind_leader_preference_pair': 21, 'candidate_generation_error_case': 8, 'context_market_state_or_fact_case': 17, 'memory_claim': 2, 'negative_control_case': 13, 'newsless_or_unexplained_case': 43, 'ranking_error_case': 1, 'supervised_direct_event_case': 28, 'supervised_issuer_day_case': 29, 'theme_formation_case': 7}, 'record_counts_by_typed_payload_status': {'KNOWN_TYPED_PAYLOAD': 169}, 'record_counts_by_evidence_phase': {'AUDIT': 2, 'POSTMORTEM': 167}, 'record_counts_by_training_target': {'candidate_generation_correction': 8, 'candidate_ranking_correction': 1, 'context_market_state_or_fact': 17, 'direct_event_response': 28, 'issuer_day_price_response': 29, 'legacy_catalog_only': 2, 'negative_control_calibration': 13, 'newsless_outcome_calibration': 43, 'outcome_preferred_candidate': 21, 'theme_formation_response': 7}, 'unknown_typed_payload_count': 0, 'raw_only_record_count': 0, 'ineligible_record_count': 25}`
- staged_stats: `{'record_count': 0, 'episode_count': 0, 'training_eligible_record_count': 0, 'record_counts_by_type': {}, 'record_counts_by_typed_payload_status': {}, 'record_counts_by_evidence_phase': {}, 'record_counts_by_training_target': {}, 'unknown_typed_payload_count': 0, 'raw_only_record_count': 0, 'ineligible_record_count': 0}`
