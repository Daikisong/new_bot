# Bundle Smoke Report

- schema_version: `nslab.real_bundle_smoke.v1`
- status: `synthetic_only`
- passed: `False`
- real_smoke_pending: `True`
## search_order

- `data_inbox`
- `tests_fixture`
- `env`
- `cli`

- environment_key: `NSLAB_REAL_BUNDLE_PATH`
## search_locations

- `{'source': 'data_inbox', 'path': 'data/inbox/research', 'exists': True, 'is_file': False, 'is_dir': True, 'configured': False}`
- `{'source': 'tests_fixture', 'path': 'tests/fixtures/research_bundles', 'exists': True, 'is_file': False, 'is_dir': True, 'configured': False}`

- candidate_count: `1`
- inspected_count: `1`
- valid_smoke_count: `1`
- real_valid_smoke_count: `0`
- synthetic_valid_smoke_count: `1`
- failed_inspection_count: `0`
- production_failed_inspection_count: `0`
- first_production_source: `None`
- first_production_status: `None`
- first_production_path: `None`
## first_production_failure_reasons

- none

- selected: `None`
## inspections

- `{'source': 'tests_fixture', 'path': 'tests/fixtures/research_bundles/synthetic_v11_bundle.md', 'absolute_path': 'C:/Users/eorb9/projects/news_bot/tests/fixtures/research_bundles/synthetic_v11_bundle.md', 'status': 'passed', 'inspectable': True, 'production_source': False, 'inspection': {'status': 'passed', 'v11_accept_full_smoke_passed': True, 'direct_ingest_smoke_passed': True, 'failure_reason_count': 0, 'failure_reasons': [], 'direct_ingest_failure_reason_count': 0, 'direct_ingest_failure_reasons': [], 'raw_bundle_sha256': '13d0367d1f70fd7d18fc65bd7738d501cc0479b20a12721c9c7b7e17c46f312e', 'bundle_version': 'nslab.research_bundle.v11', 'manifest_schema_version': 'nslab.bundle_manifest.v11', 'episode_schema_version': 'nslab.research_episode.v11', 'adapter': 'v11', 'supported': True, 'episode_id': 'NSLAB-20300110-SYNTH', 'trade_date': '2030-01-10', 'raw_record_count': 2, 'normalized_record_count': 2, 'training_eligible_record_count': 2, 'raw_record_ids': ['BRAIN-SYNTH-ISSUER', 'BRAIN-SYNTH-PAIR'], 'normalized_record_ids': ['BRAIN-SYNTH-ISSUER', 'BRAIN-SYNTH-PAIR'], 'raw_record_without_id_count': 0, 'record_id_set_comparable': True, 'record_id_set_matches_raw': True, 'missing_normalized_record_ids': [], 'extra_normalized_record_ids': [], 'raw_record_counts_by_type': {'blind_leader_preference_pair': 1, 'supervised_issuer_day_case': 1}, 'record_type_counts_match_raw': True, 'raw_training_eligible_record_count': 2, 'training_eligible_count_matches_raw': True, 'raw_payload_hashes_match': True, 'raw_payload_hash_mismatch_record_ids': [], 'import_loss_audit_passed': True, 'typed_payload_valid': True, 'invalid_typed_payload_record_count': 0, 'dropped_record_count': 0, 'quarantined_bundle_count': 0, 'quarantined_raw_record_count': 0, 'quarantined_record_count': 0, 'record_counts_by_type': {'blind_leader_preference_pair': 1, 'supervised_issuer_day_case': 1}, 'validation_passed': True, 'bundle_status_accept_full': True, 'blind_valid': True, 'validator_exit_code_zero': True, 'critical_error_count_zero': True, 'record_count_matches_manifest': True, 'training_eligible_count_matches_manifest': True, 'available_from_valid': True, 'invalid_available_from_record_count': 0, 'outcome_label_quality_valid': True, 'invalid_outcome_label_quality_record_count': 0, 'hash_mismatch_count': 0, 'hash_expectation_conflict_count': 0, 'missing_source_reference_count': 0, 'missing_payload_reference_count': 0, 'direct_ingest_contract_present': True, 'direct_ingest_contract_schema_version': 'nslab.direct_ingest_contract.v1', 'direct_brain_ingest_ready': True, 'brain_eligible': True, 'requires_human_semantic_review': False, 'direct_ingest_fatal_blocker_count': 0, 'direct_ingest_contract_validation_parity_verified': True, 'direct_ingest_contract_count_hash_parity_verified': True, 'final_semantic_audit_present': True, 'final_semantic_audit_count': 2, 'final_semantic_audit_fail_count': 0}}`
