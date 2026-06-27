# Brain Compile Report

- accepted_episode_count: `2`
- brain_version: `brain-b5e0bdca00`
- catalog_only: `True`
## category_claim_counts

- beneficiary_discovery: `0`
- continuation: `0`
- counterexamples: `0`
- failure_modes: `0`
- leader_selection: `1`
- market_memory: `2`
- single_event: `1`
- theme_formation: `0`
- world_model: `4`

## category_claim_ids

- beneficiary_discovery: `[]`
- continuation: `[]`
- counterexamples: `[]`
- failure_modes: `[]`
- leader_selection: `['CC-1c88f25cda7b']`
- market_memory: `['CC-eb0220461892', 'CC-57d0a61fda13']`
- single_event: `['CC-d0eb1ee3fcd4']`
- theme_formation: `[]`
- world_model: `['CC-d0eb1ee3fcd4', 'CC-1c88f25cda7b', 'CC-eb0220461892', 'CC-57d0a61fda13']`

- category_file_count: `9`
## category_files

- `00_world_model.md`
- `01_single_event_patterns.md`
- `02_theme_formation_patterns.md`
- `03_beneficiary_discovery.md`
- `04_leader_selection.md`
- `05_continuation_patterns.md`
- `06_failure_modes.md`
- `07_counterexamples.md`
- `08_market_memory.md`

## category_source_record_counts

- beneficiary_discovery: `0`
- continuation: `2`
- counterexamples: `0`
- failure_modes: `0`
- leader_selection: `1`
- market_memory: `2`
- single_event: `1`
- theme_formation: `0`
- world_model: `4`

## category_source_record_type_counts

- beneficiary_discovery: `{}`
- continuation: `{'memory_claim': 2}`
- counterexamples: `{}`
- failure_modes: `{}`
- leader_selection: `{'blind_leader_preference_pair': 1}`
- market_memory: `{'memory_claim': 2}`
- single_event: `{'supervised_issuer_day_case': 1}`
- theme_formation: `{}`
- world_model: `{'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}`

- claim_count: `7`
- compiled_claim_count: `4`
- compiled_claims_file_present: `True`
- compiler_mode: `catalog`
- compiler_model: `nslab.brain.catalog.compiler.v5`
- compiler_provider: `deterministic_catalog`
- compiler_version: `nslab.brain.catalog.compiler.v5`
- covered_episode_count: `2`
## latest_brain_audit

- deep: `True`
- passed: `True`
- brain_version: `brain-b5e0bdca00`
- brain_build_mode: `catalog`
- catalog_only: `True`
- coverage_complete: `True`
- record_coverage_complete: `True`
- deterministic_rebuild_verified: `True`
- llm_compile_manifest_present: `False`
- llm_compile_manifest_schema_version: `None`
- llm_compile_expected_manifest_schema_version: `nslab.llm_full_brain_compile_manifest.v1`
- llm_compile_category_schema_mismatches: `[]`
- compiled_claim_file_present: `True`
- brain_category_file_count: `9`
- brain_category_missing_files: `[]`
- brain_category_source_record_types: `{'beneficiary_discovery': {}, 'continuation': {'memory_claim': 2}, 'counterexamples': {}, 'failure_modes': {}, 'leader_selection': {'blind_leader_preference_pair': 1}, 'market_memory': {'memory_claim': 2}, 'single_event': {'supervised_issuer_day_case': 1}, 'theme_formation': {}, 'world_model': {'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}}`
- brain_category_source_population_mismatches: `[]`
- brain_empty_category_complete_files: `[]`
- brain_category_files_identical: `[]`
- brain_category_bodies_identical: `[]`
- finding_count: `0`
- findings: `[]`

- llm_compile: `None`
- llm_compile_present: `False`
- llm_compile_run: `None`
- llm_compile_run_present: `False`
## record_coverage

- accepted_record_count: `4`
- audit_only_record_count: `2`
- available_record_count: `4`
- available_record_count_as_of: `2`
- compiled_record_count: `4`
- coverage_complete: `True`
- ineligible_record_count: `2`
- record_counts_by_evidence_phase: `{'AUDIT': 2, 'POSTMORTEM': 2}`
- record_counts_by_training_target: `{'issuer_day_price_response': 1, 'legacy_catalog_only': 2, 'outcome_preferred_candidate': 1}`
- record_counts_by_type: `{'blind_leader_preference_pair': 1, 'memory_claim': 2, 'supervised_issuer_day_case': 1}`
- swept_record_count: `4`
- training_eligible_available_record_count: `2`
- training_eligible_record_count_as_of: `0`
- unswept_record_ids: `[]`

- schema_version: `nslab.brain_compile_diagnostics.v1`
