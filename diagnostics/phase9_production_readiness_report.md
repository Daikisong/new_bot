# Phase9 Production Readiness Report

- schema_version: `nslab.phase9_production_readiness.v1`
- ready: `False`
- blocker_count: `5`
## blockers

- `Phase 8 production shadow gate is not ready`
- `current record store does not match import-ready inventory`
- `production batch import has not been staged`
- `production import inventory is not attested`
- `production release is not active`

- inventory_id: `P9INV-974A99B55B152FF02040`
- inventory_manifest_path: `runs/production_import/inventories/P9INV-974A99B55B152FF02040/production_import_inventory.json`
- inventory_ready: `True`
- inventory_current: `True`
- inventory_attested: `False`
- ready_bundle_count: `1127`
- ready_record_count: `606737`
- ready_training_eligible_record_count: `384846`
- current_record_count: `968`
- staged_import_receipt_count: `0`
- release_manifest_count: `0`
- active_release_id: `None`
- runtime_project_root: `.`
## provider_configured

- llm: `True`
- llm_model: `True`
- embedding: `True`
- web: `True`
- price: `True`

- evidence_policy: `csv-memory-only-strict`
- web_required: `False`
- web_provider: `disabled`
- web_policy_status: `READY_DISABLED_BY_DESIGN`
- embedding_fallback_policy: `fail-closed`
## codex_oauth_health

- logged_in: `True`
- login_method: `chatgpt`
- status: `PASS`

## shadow_readiness

- schema_version: `nslab.shadow_replay_readiness.v1`
- prediction_date_count: `1`
- postmortem_date_count: `1`
- paired_historical_day_count: `1`
- paired_historical_dates: `['2026-06-24']`
- memory_snapshot_id: `None`
- memory_status: `missing`
- brain_version: `brain-cc5af5ba71`
- brain_build_mode: `catalog`
- production_shadow_evaluation_ids: `[]`
- checks: `{'minimum_calibration_and_holdout_days': False, 'production_memory_snapshot_ready': False, 'llm_full_production_brain': False, 'real_llm_provider_configured': True, 'real_price_provider_configured': True, 'csv_memory_only_evidence_policy': True, 'web_disabled_by_design': True, 'real_web_provider_configured': True, 'shadow_pre_registration_key_configured': True, 'shadow_runner_attestation_key_configured': True, 'shadow_truth_attestation_key_configured': True, 'actual_a_to_f_source_closure_available': False}`
- ready: `False`
- blockers: `['actual_a_to_f_source_closure_available', 'llm_full_production_brain', 'minimum_calibration_and_holdout_days', 'production_memory_snapshot_ready']`
