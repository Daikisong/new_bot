# Training Export Report

- schema_version: `nslab.training_export_diagnostics.v1`
- passed: `True`
## findings

- none

## export_kinds

- `evals`
- `preference`
- `sft`

## available_manifest_kinds

- `evals`
- `preference`
- `sft`

## missing_manifest_kinds

- none

- source_episode_count: `3`
- source_record_count: `4`
- eligible_record_count: `3`
- exported_record_count: `3`
- row_count: `3`
- skipped_record_count: `9`
- per_export_eligible_record_count: `3`
- per_export_exported_record_count: `3`
- per_export_skipped_record_count: `9`
- unique_source_record_count: `4`
- unique_training_eligible_record_count: `2`
- unique_exported_record_count: `2`
- unique_skipped_record_count: `2`
## unique_exported_record_ids

- `BRAIN-SYNTH-ISSUER`
- `BRAIN-SYNTH-PAIR`

## unique_skipped_record_ids

- `EP-2991eb145f59:legacy_catalog_record`
- `EP-f56c790fe2fc:legacy_catalog_record`

- blind_safe_row_count: `0`
- hindsight_row_count: `3`
## source_phase_counts

- POSTMORTEM: `3`

## counts_by_record_type

- blind_leader_preference_pair: `1`
- memory_claim: `2`
- supervised_issuer_day_case: `1`

## counts_by_training_target

- issuer_day_price_response: `1`
- legacy_catalog_only: `2`
- outcome_preferred_candidate: `1`

## weight_validation_statuses

- evals: `passed`
- preference: `passed`
- sft: `passed`

## exports

- evals: `{'kind': 'evals', 'source_mode': 'brain_records', 'source_episode_count': 3, 'source_record_count': 4, 'eligible_record_count': 1, 'exported_record_count': 1, 'row_count': 1, 'skipped_record_count': 3, 'blind_safe_row_count': 0, 'hindsight_row_count': 1, 'counts_by_record_type': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}, 'counts_by_training_target': {'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}, 'category_counts': {'evaluation_examples': 1}, 'missing_training_categories': [], 'source_phase_counts': {'POSTMORTEM': 1}, 'weight_validation_status': 'passed', 'output_file': 'training_exports/evals/evals.jsonl'}`
- preference: `{'kind': 'preference', 'source_mode': 'brain_records', 'source_episode_count': 3, 'source_record_count': 4, 'eligible_record_count': 1, 'exported_record_count': 1, 'row_count': 1, 'skipped_record_count': 3, 'blind_safe_row_count': 0, 'hindsight_row_count': 1, 'counts_by_record_type': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}, 'counts_by_training_target': {'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}, 'category_counts': {'positive_vs_negative_candidate_preferences': 1}, 'missing_training_categories': [], 'source_phase_counts': {'POSTMORTEM': 1}, 'weight_validation_status': 'passed', 'output_file': 'training_exports/preference/preference.jsonl'}`
- sft: `{'kind': 'sft', 'source_mode': 'brain_records', 'source_episode_count': 3, 'source_record_count': 4, 'eligible_record_count': 1, 'exported_record_count': 1, 'row_count': 1, 'skipped_record_count': 3, 'blind_safe_row_count': 0, 'hindsight_row_count': 1, 'counts_by_record_type': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}, 'counts_by_training_target': {'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}, 'category_counts': {'beneficiary_discovery_examples': 0, 'blind_reasoning_examples': 0, 'direct_event_supervised_records': 0, 'failure_correction_examples': 0, 'issuer_day_supervised_records': 1, 'leader_selection_comparisons': 0, 'theme_formation_examples': 0}, 'missing_training_categories': ['blind_reasoning_examples', 'theme_formation_examples', 'beneficiary_discovery_examples', 'leader_selection_comparisons', 'failure_correction_examples', 'direct_event_supervised_records'], 'source_phase_counts': {'POSTMORTEM': 1}, 'weight_validation_status': 'passed', 'output_file': 'training_exports/sft/sft.jsonl'}`
