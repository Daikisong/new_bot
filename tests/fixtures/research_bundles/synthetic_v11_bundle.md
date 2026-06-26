---
schema_version: nslab.research_bundle.v11
episode_id: NSLAB-20300110-SYNTH
trade_date: 2030-01-10
cutoff_at: 2030-01-10T08:59:59+09:00
bundle_status: ACCEPT_FULL
blind_valid: true
---
<!-- NSLAB:BEGIN research_episode.json -->
```json
{"available_from": "2030-01-11T00:00:00+09:00", "cutoff_at": "2030-01-10T08:59:59+09:00", "episode_id": "NSLAB-20300110-SYNTH", "schema_version": "nslab.research_episode.v11", "trade_date": "2030-01-10"}
```
<!-- NSLAB:END research_episode.json -->

<!-- NSLAB:BEGIN brain_delta.jsonl -->
```jsonl
{"record_id": "BRAIN-SYNTH-ISSUER", "record_type": "supervised_issuer_day_case", "episode_id": "NSLAB-20300110-SYNTH", "trade_date": "2030-01-10", "available_from": "2030-01-11T00:00:00+09:00", "status": "tentative", "confidence_label": "low", "training_target": "issuer_day_price_response", "issuer_day_case_id": "20300110:000001", "ticker": "000001", "company_name": "Synthetic Issuer", "response_class": "positive_high10", "sample_weight": 1.0, "training_eligible": true, "eligibility_reason": "synthetic verified label", "provenance_source_ids": ["SRC-SYNTH-1"]}
{"record_id": "BRAIN-SYNTH-PAIR", "record_type": "blind_leader_preference_pair", "episode_id": "NSLAB-20300110-SYNTH", "trade_date": "2030-01-10", "available_from": "2030-01-11T00:00:00+09:00", "training_target": "outcome_preferred_candidate", "blind_pair_id": "PAIR-SYNTH-1", "blind_preferred_candidate_id": "CAND-A", "blind_rejected_candidate_id": "CAND-B", "outcome_preferred_candidate_id": "CAND-A", "blind_preference_correct": true, "training_eligible": true, "eligibility_reason": "synthetic sealed pair", "provenance_source_ids": ["SRC-SYNTH-1"]}
```
<!-- NSLAB:END brain_delta.jsonl -->

<!-- NSLAB:BEGIN source_ledger.jsonl -->
```jsonl
{"source_id": "SRC-SYNTH-1", "source_type": "synthetic_fixture", "title": "Synthetic source"}
```
<!-- NSLAB:END source_ledger.jsonl -->

<!-- NSLAB:BEGIN validation_report.json -->
```json
{"computed_counts": {"brain_delta_record_count": 2, "training_eligible_record_count": 2}, "critical_error_count": 0, "schema_version": "nslab.validation_report.v3"}
```
<!-- NSLAB:END validation_report.json -->

<!-- NSLAB:BEGIN bundle_manifest.json -->
```json
{"blind_valid": true, "brain_delta_record_count": 2, "bundle_status": "ACCEPT_FULL", "critical_error_count": 0, "cutoff_at": "2030-01-10T08:59:59+09:00", "embedded_blocks": {"brain_delta.jsonl": {"sha256": "afcddb7772b0690da47b11ba180bf07d3f46fc3a7e457c2835bf75b5fa9fb331"}, "research_episode.json": {"sha256": "e47fe450272a683223cdc7b6581329d2fe1a27147c877e6eec811bb432bbfb06"}, "source_ledger.jsonl": {"sha256": "b338c304b1f136382541508cdc31fbfc9ebf83a356ad36b3c8cc21a6ab7346af"}, "validation_report.json": {"sha256": "659829f46239a588a393ae4efff22ca072772ab629427da20041346bdae00505"}}, "episode_id": "NSLAB-20300110-SYNTH", "schema_version": "nslab.bundle_manifest.v11", "trade_date": "2030-01-10", "training_eligible_record_count": 2, "validator_exit_code": 0}
```
<!-- NSLAB:END bundle_manifest.json -->
