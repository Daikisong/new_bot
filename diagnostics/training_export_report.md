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

- brain_record_source_required: `True`
- record_store_source_record_count: `4`
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
## unique_source_record_ids

- `BRAIN-SYNTH-ISSUER`
- `BRAIN-SYNTH-PAIR`
- `EP-2991eb145f59:legacy_catalog_record`
- `EP-f56c790fe2fc:legacy_catalog_record`

## unique_training_eligible_record_ids

- `BRAIN-SYNTH-ISSUER`
- `BRAIN-SYNTH-PAIR`

## unique_exported_record_ids

- `BRAIN-SYNTH-ISSUER`
- `BRAIN-SYNTH-PAIR`

## unique_skipped_record_ids

- `EP-2991eb145f59:legacy_catalog_record`
- `EP-f56c790fe2fc:legacy_catalog_record`

## skipped_records_by_export

- evals: `[{'record_id': 'EP-2991eb145f59:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-2991eb145f59', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'EP-f56c790fe2fc:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-f56c790fe2fc', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'BRAIN-SYNTH-PAIR', 'record_type': 'blind_leader_preference_pair', 'episode_id': 'NSLAB-20300110-SYNTH', 'training_eligible': True, 'eligibility_reason': 'synthetic sealed pair', 'reason': 'record_type_not_selected_for_export_kind', 'skip_reasons': ['record_type_not_selected_for_export_kind']}]`
- preference: `[{'record_id': 'EP-2991eb145f59:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-2991eb145f59', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'EP-f56c790fe2fc:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-f56c790fe2fc', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'BRAIN-SYNTH-ISSUER', 'record_type': 'supervised_issuer_day_case', 'episode_id': 'NSLAB-20300110-SYNTH', 'training_eligible': True, 'eligibility_reason': 'synthetic verified label', 'reason': 'record_type_not_selected_for_export_kind', 'skip_reasons': ['record_type_not_selected_for_export_kind']}]`
- sft: `[{'record_id': 'EP-2991eb145f59:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-2991eb145f59', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'EP-f56c790fe2fc:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-f56c790fe2fc', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'BRAIN-SYNTH-PAIR', 'record_type': 'blind_leader_preference_pair', 'episode_id': 'NSLAB-20300110-SYNTH', 'training_eligible': True, 'eligibility_reason': 'synthetic sealed pair', 'reason': 'record_type_not_selected_for_export_kind', 'skip_reasons': ['record_type_not_selected_for_export_kind']}]`

## skipped_record_reasons_by_record_id

- BRAIN-SYNTH-ISSUER: `['record_type_not_selected_for_export_kind']`
- BRAIN-SYNTH-PAIR: `['record_type_not_selected_for_export_kind']`
- EP-2991eb145f59:legacy_catalog_record: `['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']`
- EP-f56c790fe2fc:legacy_catalog_record: `['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']`

## unique_skipped_record_reasons_by_record_id

- EP-2991eb145f59:legacy_catalog_record: `['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']`
- EP-f56c790fe2fc:legacy_catalog_record: `['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']`

## skipped_record_reason_counts

- legacy v1 episode migrated as catalog_only memory: `6`
- record_type_not_selected_for_export_kind: `9`

- blind_safe_row_count: `0`
- hindsight_row_count: `3`
## source_phase_counts

- POSTMORTEM: `3`

## source_hashes

- EP-2991eb145f59: `4b33d532c2fc2e45eab2f1dafce485d652a8474115751837703a672c00dec0e5`
- EP-f56c790fe2fc: `c6fd6060d83590e0fe52fa5a2492283322cb9f9e4b2a88105262268cd55adbde`

## source_record_hashes

- BRAIN-SYNTH-ISSUER: `12f4f71c6617b68465deea686d868358d18f564890e508f3eef33cb3246959c9`
- BRAIN-SYNTH-PAIR: `b7df4ca0e4addb48eec28e20cead893439cbd2791c6303d4ad4da298562ad39d`
- EP-2991eb145f59:legacy_catalog_record: `7f77f299c6a2f160dc1c30e9add7f02bd3392861fcf2a70727bfedc71e13d2f5`
- EP-f56c790fe2fc:legacy_catalog_record: `d892d0d0846335c209032c87461a3a312b516f0537d4bd59e9afb70fa176586a`

- source_record_hash_count: `4`
## counts_by_record_type

- blind_leader_preference_pair: `1`
- memory_claim: `2`
- supervised_issuer_day_case: `1`

## counts_by_training_target

- issuer_day_price_response: `1`
- legacy_catalog_only: `2`
- outcome_preferred_candidate: `1`

- duplicate_issuer_day_count: `0`
## duplicate_issuer_day_keys

- none

- issuer_day_weight_sum_mismatch_count: `0`
## issuer_day_weight_sum_mismatches


- direct_event_weight_sum_mismatch_count: `0`
## direct_event_weight_sum_mismatches


## weight_validation_statuses

- evals: `passed`
- preference: `passed`
- sft: `passed`

## exports

- evals: `{'kind': 'evals', 'source_mode': 'brain_records', 'source_episode_count': 3, 'source_record_count': 4, 'source_record_ids': ['BRAIN-SYNTH-ISSUER', 'BRAIN-SYNTH-PAIR', 'EP-2991eb145f59:legacy_catalog_record', 'EP-f56c790fe2fc:legacy_catalog_record'], 'eligible_record_count': 1, 'eligible_record_ids': ['BRAIN-SYNTH-ISSUER'], 'exported_record_count': 1, 'exported_record_ids': ['BRAIN-SYNTH-ISSUER'], 'row_count': 1, 'skipped_record_count': 3, 'skipped_record_ids': ['BRAIN-SYNTH-PAIR', 'EP-2991eb145f59:legacy_catalog_record', 'EP-f56c790fe2fc:legacy_catalog_record'], 'skipped_records': [{'record_id': 'EP-2991eb145f59:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-2991eb145f59', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'EP-f56c790fe2fc:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-f56c790fe2fc', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'BRAIN-SYNTH-PAIR', 'record_type': 'blind_leader_preference_pair', 'episode_id': 'NSLAB-20300110-SYNTH', 'training_eligible': True, 'eligibility_reason': 'synthetic sealed pair', 'reason': 'record_type_not_selected_for_export_kind', 'skip_reasons': ['record_type_not_selected_for_export_kind']}], 'blind_safe_row_count': 0, 'hindsight_row_count': 1, 'counts_by_record_type': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}, 'counts_by_training_target': {'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}, 'category_counts': {'evaluation_examples': 1}, 'missing_training_categories': [], 'source_phase_counts': {'POSTMORTEM': 1}, 'source_hashes': {'EP-2991eb145f59': '4b33d532c2fc2e45eab2f1dafce485d652a8474115751837703a672c00dec0e5', 'EP-f56c790fe2fc': 'c6fd6060d83590e0fe52fa5a2492283322cb9f9e4b2a88105262268cd55adbde'}, 'source_record_hashes': {'BRAIN-SYNTH-ISSUER': '12f4f71c6617b68465deea686d868358d18f564890e508f3eef33cb3246959c9', 'BRAIN-SYNTH-PAIR': 'b7df4ca0e4addb48eec28e20cead893439cbd2791c6303d4ad4da298562ad39d', 'EP-2991eb145f59:legacy_catalog_record': '7f77f299c6a2f160dc1c30e9add7f02bd3392861fcf2a70727bfedc71e13d2f5', 'EP-f56c790fe2fc:legacy_catalog_record': 'd892d0d0846335c209032c87461a3a312b516f0537d4bd59e9afb70fa176586a'}, 'source_record_hash_count': 4, 'weight_validation_status': 'passed', 'duplicate_issuer_day_count': 0, 'duplicate_issuer_day_keys': [], 'issuer_day_weight_sum_mismatch_count': 0, 'issuer_day_weight_sum_mismatches': {}, 'direct_event_weight_sum_mismatch_count': 0, 'direct_event_weight_sum_mismatches': {}, 'output_file': 'training_exports/evals/evals.jsonl'}`
- preference: `{'kind': 'preference', 'source_mode': 'brain_records', 'source_episode_count': 3, 'source_record_count': 4, 'source_record_ids': ['BRAIN-SYNTH-ISSUER', 'BRAIN-SYNTH-PAIR', 'EP-2991eb145f59:legacy_catalog_record', 'EP-f56c790fe2fc:legacy_catalog_record'], 'eligible_record_count': 1, 'eligible_record_ids': ['BRAIN-SYNTH-PAIR'], 'exported_record_count': 1, 'exported_record_ids': ['BRAIN-SYNTH-PAIR'], 'row_count': 1, 'skipped_record_count': 3, 'skipped_record_ids': ['BRAIN-SYNTH-ISSUER', 'EP-2991eb145f59:legacy_catalog_record', 'EP-f56c790fe2fc:legacy_catalog_record'], 'skipped_records': [{'record_id': 'EP-2991eb145f59:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-2991eb145f59', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'EP-f56c790fe2fc:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-f56c790fe2fc', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'BRAIN-SYNTH-ISSUER', 'record_type': 'supervised_issuer_day_case', 'episode_id': 'NSLAB-20300110-SYNTH', 'training_eligible': True, 'eligibility_reason': 'synthetic verified label', 'reason': 'record_type_not_selected_for_export_kind', 'skip_reasons': ['record_type_not_selected_for_export_kind']}], 'blind_safe_row_count': 0, 'hindsight_row_count': 1, 'counts_by_record_type': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}, 'counts_by_training_target': {'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}, 'category_counts': {'positive_vs_negative_candidate_preferences': 1}, 'missing_training_categories': [], 'source_phase_counts': {'POSTMORTEM': 1}, 'source_hashes': {'EP-2991eb145f59': '4b33d532c2fc2e45eab2f1dafce485d652a8474115751837703a672c00dec0e5', 'EP-f56c790fe2fc': 'c6fd6060d83590e0fe52fa5a2492283322cb9f9e4b2a88105262268cd55adbde'}, 'source_record_hashes': {'BRAIN-SYNTH-ISSUER': '12f4f71c6617b68465deea686d868358d18f564890e508f3eef33cb3246959c9', 'BRAIN-SYNTH-PAIR': 'b7df4ca0e4addb48eec28e20cead893439cbd2791c6303d4ad4da298562ad39d', 'EP-2991eb145f59:legacy_catalog_record': '7f77f299c6a2f160dc1c30e9add7f02bd3392861fcf2a70727bfedc71e13d2f5', 'EP-f56c790fe2fc:legacy_catalog_record': 'd892d0d0846335c209032c87461a3a312b516f0537d4bd59e9afb70fa176586a'}, 'source_record_hash_count': 4, 'weight_validation_status': 'passed', 'duplicate_issuer_day_count': 0, 'duplicate_issuer_day_keys': [], 'issuer_day_weight_sum_mismatch_count': 0, 'issuer_day_weight_sum_mismatches': {}, 'direct_event_weight_sum_mismatch_count': 0, 'direct_event_weight_sum_mismatches': {}, 'output_file': 'training_exports/preference/preference.jsonl'}`
- sft: `{'kind': 'sft', 'source_mode': 'brain_records', 'source_episode_count': 3, 'source_record_count': 4, 'source_record_ids': ['BRAIN-SYNTH-ISSUER', 'BRAIN-SYNTH-PAIR', 'EP-2991eb145f59:legacy_catalog_record', 'EP-f56c790fe2fc:legacy_catalog_record'], 'eligible_record_count': 1, 'eligible_record_ids': ['BRAIN-SYNTH-ISSUER'], 'exported_record_count': 1, 'exported_record_ids': ['BRAIN-SYNTH-ISSUER'], 'row_count': 1, 'skipped_record_count': 3, 'skipped_record_ids': ['BRAIN-SYNTH-PAIR', 'EP-2991eb145f59:legacy_catalog_record', 'EP-f56c790fe2fc:legacy_catalog_record'], 'skipped_records': [{'record_id': 'EP-2991eb145f59:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-2991eb145f59', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'EP-f56c790fe2fc:legacy_catalog_record', 'record_type': 'memory_claim', 'episode_id': 'EP-f56c790fe2fc', 'training_eligible': False, 'eligibility_reason': 'legacy v1 episode migrated as catalog_only memory', 'reason': 'legacy v1 episode migrated as catalog_only memory', 'skip_reasons': ['legacy v1 episode migrated as catalog_only memory', 'record_type_not_selected_for_export_kind']}, {'record_id': 'BRAIN-SYNTH-PAIR', 'record_type': 'blind_leader_preference_pair', 'episode_id': 'NSLAB-20300110-SYNTH', 'training_eligible': True, 'eligibility_reason': 'synthetic sealed pair', 'reason': 'record_type_not_selected_for_export_kind', 'skip_reasons': ['record_type_not_selected_for_export_kind']}], 'blind_safe_row_count': 0, 'hindsight_row_count': 1, 'counts_by_record_type': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}, 'counts_by_training_target': {'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}, 'category_counts': {'beneficiary_discovery_examples': 0, 'blind_reasoning_examples': 0, 'direct_event_supervised_records': 0, 'failure_correction_examples': 0, 'issuer_day_supervised_records': 1, 'leader_selection_comparisons': 0, 'theme_formation_examples': 0}, 'missing_training_categories': ['blind_reasoning_examples', 'theme_formation_examples', 'beneficiary_discovery_examples', 'leader_selection_comparisons', 'failure_correction_examples', 'direct_event_supervised_records'], 'source_phase_counts': {'POSTMORTEM': 1}, 'source_hashes': {'EP-2991eb145f59': '4b33d532c2fc2e45eab2f1dafce485d652a8474115751837703a672c00dec0e5', 'EP-f56c790fe2fc': 'c6fd6060d83590e0fe52fa5a2492283322cb9f9e4b2a88105262268cd55adbde'}, 'source_record_hashes': {'BRAIN-SYNTH-ISSUER': '12f4f71c6617b68465deea686d868358d18f564890e508f3eef33cb3246959c9', 'BRAIN-SYNTH-PAIR': 'b7df4ca0e4addb48eec28e20cead893439cbd2791c6303d4ad4da298562ad39d', 'EP-2991eb145f59:legacy_catalog_record': '7f77f299c6a2f160dc1c30e9add7f02bd3392861fcf2a70727bfedc71e13d2f5', 'EP-f56c790fe2fc:legacy_catalog_record': 'd892d0d0846335c209032c87461a3a312b516f0537d4bd59e9afb70fa176586a'}, 'source_record_hash_count': 4, 'weight_validation_status': 'passed', 'duplicate_issuer_day_count': 0, 'duplicate_issuer_day_keys': [], 'issuer_day_weight_sum_mismatch_count': 0, 'issuer_day_weight_sum_mismatches': {}, 'direct_event_weight_sum_mismatch_count': 0, 'direct_event_weight_sum_mismatches': {}, 'output_file': 'training_exports/sft/sft.jsonl'}`
