---
schema_version: "nslab.research_bundle.v11"
artifact_type: "research_episode_bundle"
episode_id: "NSLAB-20201030-ACQUIRE-FAILED-20260706T125201Z"
trade_date: "2020-10-30"
calendar_date: "2020-10-30"
bundle_status: "ACQUIRE_FAILED_START_CURSOR_ABSENT"
brain_eligible: false
direct_brain_ingest_ready: false
outcome_research_performed: false
requested_input_file: "news_20201030.csv"
selected_input_file: null
fatal_blockers: ["START_CURSOR_FILE_ABSENT"]
created_at: "2026-07-06T21:56:12+09:00"
---

# NSLAB Episode Bundle — Acquisition Failed

## status

```json
{
  "status": "ACQUIRE_FAILED_START_CURSOR_ABSENT",
  "brain_eligible": false,
  "direct_brain_ingest_ready": false,
  "outcome_research_performed": false,
  "requested_input_file": "news_20201030.csv",
  "selected_input_file": null,
  "fatal_blockers": [
    "START_CURSOR_FILE_ABSENT"
  ],
  "substitution_performed": false
}
```

## acquisition_report.json

```json
{
  "schema_version": "nslab.acquisition_report.v1",
  "run_directory": "/mnt/data/nslab_run_20260706T125201Z",
  "created_at": "2026-07-06T21:56:12+09:00",
  "main_prompt": {
    "status": "ACQUIRED_VERIFIED",
    "input_file": "research_prompt_20260706T125201Z_48a34183.md",
    "sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
    "byte_size": 430066,
    "expected_sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
    "expected_byte_size": 430066,
    "expected_title": "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER",
    "actual_title": "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER",
    "version_match": true
  },
  "news_csv": {
    "requested_input_file": "news_20201030.csv",
    "status": "ACQUIRE_FAILED_START_CURSOR_ABSENT",
    "selected_input_file": null,
    "input_sha256": null,
    "input_byte_size": null,
    "csv_row_count": null,
    "parsed_row_count": null,
    "columns": null,
    "min_published_at": null,
    "max_published_at": null,
    "time_unverified_rows": null,
    "control_char_count": null,
    "reason": "Exact requested CSV was not obtainable from the prescribed main-branch paths and permitted fallbacks in this session.",
    "substitution_performed": false,
    "other_date_csv_used": false,
    "sandbox_residual_file_used": false
  },
  "acquisition_warnings": [
    "CONTENTS_API_WEB_OPEN_SAFE_BLOCKED",
    "CONTENTS_API_CURL_DNS_FAILURE_WARNING_ONLY",
    "CODELOAD_REDIRECT_SAFE_BLOCKED",
    "CODELOAD_DOWNLOAD_TOOL_BLOCKED_UNVIEWED_URL"
  ],
  "attempts": [
    {
      "attempt_id": "ACQ-001",
      "logical_role": "MAIN_EXECUTION_PROMPT",
      "method": "web.open raw.githubusercontent refs/heads/main",
      "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md",
      "status": "WEB_VIEW_OK",
      "evidence": "Raw text/plain opened; first line visible through web layer."
    },
    {
      "attempt_id": "ACQ-002",
      "logical_role": "MAIN_EXECUTION_PROMPT",
      "method": "download_tool_save_temp",
      "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md",
      "local_path": "/mnt/data/nslab_run_20260706T125201Z/research_prompt_20260706T125201Z_48a34183.md",
      "status": "DOWNLOAD_OK",
      "byte_size": 430066,
      "sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
      "expected_sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
      "expected_byte_size": 430066,
      "title": "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER"
    },
    {
      "attempt_id": "ACQ-003",
      "logical_role": "NEWS_CSV",
      "method": "web.open raw.githubusercontent refs/heads/main",
      "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20201030.csv",
      "status": "HTTP_404_NOT_FOUND",
      "evidence": "web layer returned 404 Not Found for exact requested raw URL."
    },
    {
      "attempt_id": "ACQ-004",
      "logical_role": "NEWS_CSV",
      "method": "web.open raw.githubusercontent main shorthand",
      "url": "https://raw.githubusercontent.com/Daikisong/new_bot/main/docs/csv/news_20201030.csv",
      "status": "HTTP_404_NOT_FOUND",
      "evidence": "web layer returned 404 Not Found for exact requested raw shorthand URL."
    },
    {
      "attempt_id": "ACQ-005",
      "logical_role": "NEWS_CSV",
      "method": "web.open GitHub blob page",
      "url": "https://github.com/Daikisong/new_bot/blob/main/docs/csv/news_20201030.csv",
      "status": "HTTP_404_NOT_FOUND",
      "evidence": "web layer returned 404 Not Found for exact requested blob path."
    },
    {
      "attempt_id": "ACQ-006",
      "logical_role": "NEWS_CSV",
      "method": "web.open GitHub raw endpoint",
      "url": "https://github.com/Daikisong/new_bot/raw/main/docs/csv/news_20201030.csv",
      "status": "HTTP_404_NOT_FOUND",
      "evidence": "web layer returned 404 Not Found for exact requested GitHub raw endpoint."
    },
    {
      "attempt_id": "ACQ-007",
      "logical_role": "NEWS_CSV",
      "method": "download_tool_save_temp",
      "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20201030.csv",
      "status": "DOWNLOAD_BLOCKED_AFTER_404_WEB_VIEW",
      "evidence": "download tool refused because no successful prior view existed for the 404 raw URL; no local CSV bytes were saved."
    },
    {
      "attempt_id": "ACQ-008",
      "logical_role": "NEWS_CSV",
      "method": "web.open GitHub Contents API exact path",
      "url": "https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_20201030.csv?ref=main",
      "status": "WEB_OPEN_SAFE_BLOCKED",
      "evidence": "web layer did not permit opening the API URL directly; no CSV bytes were obtained."
    },
    {
      "attempt_id": "ACQ-009",
      "logical_role": "NEWS_CSV",
      "method": "curl diagnostic Contents API exact path",
      "url": "https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_20201030.csv?ref=main",
      "status": "DNS_FAILURE_WARNING_ONLY",
      "evidence": "curl: (6) Could not resolve host: api.github.com. Per bootstrap, this was recorded as a warning, not as proof of absence."
    },
    {
      "attempt_id": "ACQ-010",
      "logical_role": "NEWS_CSV",
      "method": "web.open GitHub archive ZIP fallback",
      "url": "https://github.com/Daikisong/new_bot/archive/refs/heads/main.zip",
      "status": "REDIRECT_SAFE_BLOCKED",
      "evidence": "web layer reached GitHub archive redirect to codeload but refused redirected codeload target as unsafe to open; no ZIP bytes were saved."
    },
    {
      "attempt_id": "ACQ-011",
      "logical_role": "NEWS_CSV",
      "method": "download_tool codeload ZIP fallback",
      "url": "https://codeload.github.com/Daikisong/new_bot/zip/refs/heads/main",
      "status": "DOWNLOAD_BLOCKED_UNVIEWED_URL",
      "evidence": "download tool refused because the codeload URL could not be successfully viewed first; no ZIP bytes were saved."
    },
    {
      "attempt_id": "ACQ-012",
      "logical_role": "NEWS_CSV",
      "method": "web.search exact start cursor path",
      "url": null,
      "status": "NO_RELEVANT_REPO_RESULT",
      "evidence": "Exact searches for news_20201030.csv in Daikisong/new_bot did not return the requested file."
    }
  ]
}
```

## bundle_manifest.json

```json
{
  "schema_version": "nslab.bundle_manifest.v11",
  "episode_id": "NSLAB-20201030-ACQUIRE-FAILED-20260706T125201Z",
  "trade_date": "2020-10-30",
  "artifact_type": "research_episode_bundle",
  "bundle_status": "ACQUIRE_FAILED_START_CURSOR_ABSENT",
  "brain_eligible": false,
  "direct_brain_ingest_ready": false,
  "requested_input_file": "news_20201030.csv",
  "selected_input_file": null,
  "fatal_blockers": [
    "START_CURSOR_FILE_ABSENT"
  ],
  "record_counts": {
    "source_ledger": 0,
    "row_disposition": 0,
    "material_review_queue": 0,
    "material_reviewed": 0,
    "candidate_screening": 0,
    "final_watchlist": 0,
    "outcome_ledger": 0,
    "outcome_leader_census": 0,
    "brain_delta": 0
  },
  "input_provenance": {
    "main_prompt_sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
    "main_prompt_byte_size": 430066,
    "news_csv_sha256": null,
    "news_csv_byte_size": null
  }
}
```

## direct_ingest_contract.json

```json
{
  "schema_version": "nslab.direct_ingest_contract.v1",
  "episode_id": "NSLAB-20201030-ACQUIRE-FAILED-20260706T125201Z",
  "direct_brain_ingest_ready": false,
  "automated_import_expected_to_pass": false,
  "brain_eligible": false,
  "requires_manual_research_review": true,
  "requires_posthoc_prompt_repair": false,
  "requires_human_semantic_review": false,
  "fatal_blockers": [
    "START_CURSOR_FILE_ABSENT"
  ],
  "hard_gate_summary": {
    "schema_contract_verified": true,
    "record_count_hash_parity_ready": false,
    "direct_ingest_contract_validation_parity_verified": true,
    "direct_ingest_contract_count_hash_parity_verified": false,
    "sample_weight_validation_status": "not_applicable_acquire_failed",
    "issuer_day_weight_sum_mismatches": {},
    "direct_event_weight_sum_mismatches": {},
    "training_provenance_closure_status": "not_applicable_acquire_failed",
    "training_eligible_empty_provenance_count": 0,
    "training_eligible_unresolved_source_count": 0,
    "validator_exit_code": 0,
    "critical_error_count": 1
  },
  "status_reason": "Requested start cursor CSV was absent/unavailable after prescribed acquisition fallbacks; ACCEPT_FULL is forbidden."
}
```

## validation_report.json

```json
{
  "schema_version": "nslab.validation_report.v1",
  "created_at": "2026-07-06T21:56:12+09:00",
  "validator_scope": "acquire_failed_minimum_bundle",
  "bundle_status": "ACQUIRE_FAILED_START_CURSOR_ABSENT",
  "accept_full_allowed": false,
  "checks": [
    {
      "check_id": "main_prompt_sha256_matches_expected",
      "expected": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
      "actual": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd",
      "passed": true
    },
    {
      "check_id": "main_prompt_byte_size_matches_expected",
      "expected": 430066,
      "actual": 430066,
      "passed": true
    },
    {
      "check_id": "main_prompt_title_matches_expected",
      "expected": "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER",
      "actual": "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER",
      "passed": true
    },
    {
      "check_id": "requested_csv_not_substituted",
      "expected": false,
      "actual": false,
      "passed": true
    },
    {
      "check_id": "selected_input_file_is_null_on_absent_start_cursor",
      "expected": null,
      "actual": null,
      "passed": true
    },
    {
      "check_id": "fatal_blocker_start_cursor_file_absent",
      "expected": [
        "START_CURSOR_FILE_ABSENT"
      ],
      "actual": [
        "START_CURSOR_FILE_ABSENT"
      ],
      "passed": true
    },
    {
      "check_id": "outcome_research_not_performed",
      "expected": false,
      "actual": false,
      "passed": true
    }
  ],
  "critical_errors": [
    "START_CURSOR_FILE_ABSENT"
  ],
  "repair_attempted": false,
  "repair_reason": "No allowed repair exists for absent requested start cursor except obtaining that exact CSV; substitution is forbidden."
}
```

## acquisition_attempts.jsonl

```jsonl
{"attempt_id": "ACQ-001", "evidence": "Raw text/plain opened; first line visible through web layer.", "logical_role": "MAIN_EXECUTION_PROMPT", "method": "web.open raw.githubusercontent refs/heads/main", "status": "WEB_VIEW_OK", "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md"}
{"attempt_id": "ACQ-002", "byte_size": 430066, "expected_byte_size": 430066, "expected_sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd", "local_path": "/mnt/data/nslab_run_20260706T125201Z/research_prompt_20260706T125201Z_48a34183.md", "logical_role": "MAIN_EXECUTION_PROMPT", "method": "download_tool_save_temp", "sha256": "48a3418387e9631f21ff8c72a8914ca9202f4643a9994d32f2bf4c15957d2cdd", "status": "DOWNLOAD_OK", "title": "# NSLAB GOLD PHASE MACHINE — DIRECT CSV RESEARCH RUNNER", "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/research_prompt.md"}
{"attempt_id": "ACQ-003", "evidence": "web layer returned 404 Not Found for exact requested raw URL.", "logical_role": "NEWS_CSV", "method": "web.open raw.githubusercontent refs/heads/main", "status": "HTTP_404_NOT_FOUND", "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20201030.csv"}
{"attempt_id": "ACQ-004", "evidence": "web layer returned 404 Not Found for exact requested raw shorthand URL.", "logical_role": "NEWS_CSV", "method": "web.open raw.githubusercontent main shorthand", "status": "HTTP_404_NOT_FOUND", "url": "https://raw.githubusercontent.com/Daikisong/new_bot/main/docs/csv/news_20201030.csv"}
{"attempt_id": "ACQ-005", "evidence": "web layer returned 404 Not Found for exact requested blob path.", "logical_role": "NEWS_CSV", "method": "web.open GitHub blob page", "status": "HTTP_404_NOT_FOUND", "url": "https://github.com/Daikisong/new_bot/blob/main/docs/csv/news_20201030.csv"}
{"attempt_id": "ACQ-006", "evidence": "web layer returned 404 Not Found for exact requested GitHub raw endpoint.", "logical_role": "NEWS_CSV", "method": "web.open GitHub raw endpoint", "status": "HTTP_404_NOT_FOUND", "url": "https://github.com/Daikisong/new_bot/raw/main/docs/csv/news_20201030.csv"}
{"attempt_id": "ACQ-007", "evidence": "download tool refused because no successful prior view existed for the 404 raw URL; no local CSV bytes were saved.", "logical_role": "NEWS_CSV", "method": "download_tool_save_temp", "status": "DOWNLOAD_BLOCKED_AFTER_404_WEB_VIEW", "url": "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/docs/csv/news_20201030.csv"}
{"attempt_id": "ACQ-008", "evidence": "web layer did not permit opening the API URL directly; no CSV bytes were obtained.", "logical_role": "NEWS_CSV", "method": "web.open GitHub Contents API exact path", "status": "WEB_OPEN_SAFE_BLOCKED", "url": "https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_20201030.csv?ref=main"}
{"attempt_id": "ACQ-009", "evidence": "curl: (6) Could not resolve host: api.github.com. Per bootstrap, this was recorded as a warning, not as proof of absence.", "logical_role": "NEWS_CSV", "method": "curl diagnostic Contents API exact path", "status": "DNS_FAILURE_WARNING_ONLY", "url": "https://api.github.com/repos/Daikisong/new_bot/contents/docs/csv/news_20201030.csv?ref=main"}
{"attempt_id": "ACQ-010", "evidence": "web layer reached GitHub archive redirect to codeload but refused redirected codeload target as unsafe to open; no ZIP bytes were saved.", "logical_role": "NEWS_CSV", "method": "web.open GitHub archive ZIP fallback", "status": "REDIRECT_SAFE_BLOCKED", "url": "https://github.com/Daikisong/new_bot/archive/refs/heads/main.zip"}
{"attempt_id": "ACQ-011", "evidence": "download tool refused because the codeload URL could not be successfully viewed first; no ZIP bytes were saved.", "logical_role": "NEWS_CSV", "method": "download_tool codeload ZIP fallback", "status": "DOWNLOAD_BLOCKED_UNVIEWED_URL", "url": "https://codeload.github.com/Daikisong/new_bot/zip/refs/heads/main"}
{"attempt_id": "ACQ-012", "evidence": "Exact searches for news_20201030.csv in Daikisong/new_bot did not return the requested file.", "logical_role": "NEWS_CSV", "method": "web.search exact start cursor path", "status": "NO_RELEVANT_REPO_RESULT", "url": null}
```

## phase_stop

```text
PHASE 0 stopped before CSV full parse because the exact start cursor file could not be acquired.
No other date CSV, sandbox residual CSV, cached prompt, cached CSV, or latest CSV was used.
No BLIND candidates, price snapshots, outcome rows, postmortem records, or brain_delta records were generated.
ACCEPT_FULL is forbidden for this episode.
```
