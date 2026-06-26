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
- selected: `None`
## inspections

- `{'source': 'tests_fixture', 'path': 'tests/fixtures/research_bundles/synthetic_v11_bundle.md', 'absolute_path': 'C:/Users/eorb9/projects/news_bot/tests/fixtures/research_bundles/synthetic_v11_bundle.md', 'status': 'passed', 'inspectable': True, 'production_source': False, 'inspection': {'status': 'passed', 'v11_accept_full_smoke_passed': True, 'raw_bundle_sha256': '49c89d2ad788f2d6e75fd3c43021951e2d0196db23c63348d59005ac8fb273fa', 'bundle_version': 'nslab.research_bundle.v11', 'manifest_schema_version': 'nslab.bundle_manifest.v11', 'episode_schema_version': 'nslab.research_episode.v11', 'adapter': 'v11', 'supported': True, 'episode_id': 'NSLAB-20300110-SYNTH', 'trade_date': '2030-01-10', 'raw_record_count': 2, 'normalized_record_count': 2, 'training_eligible_record_count': 2, 'dropped_record_count': 0, 'quarantined_record_count': 0, 'record_counts_by_type': {'blind_leader_preference_pair': 1, 'supervised_issuer_day_case': 1}, 'validation_passed': True, 'bundle_status_accept_full': True, 'blind_valid': True, 'validator_exit_code_zero': True, 'critical_error_count_zero': True, 'record_count_matches_manifest': True, 'training_eligible_count_matches_manifest': True, 'available_from_valid': True, 'invalid_available_from_record_count': 0, 'hash_mismatch_count': 0, 'hash_expectation_conflict_count': 0, 'missing_source_reference_count': 0}}`
