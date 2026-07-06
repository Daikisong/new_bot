# 0708 research repair audit

Date: 2026-07-07

## Purpose

Copied the research Markdown files from these folders into `docs/0708/research_md/` and checked whether failed ingestion was caused by bad research quality or by repair/post-processing that was too narrow.

- `Downloads/2018-start`
- `Downloads/2020-start`
- `Downloads/2023-start`

## Conclusion

Most failures were post-processing and packaging problems, not empty or unusable research.

Before the patch, several bundles had hundreds of records in the original Markdown, but importer normalization reduced `training_eligible` to `0`. The main reason was that repair/import did not understand the output variants used by web research sessions.

Observed packaging and alias issues:

- record type aliases such as `direct_event_outcome`, `issuer_day_supervised`, and `issuer_day_case`
- `## ARTIFACT: brain_delta.jsonl` headings
- `## Machine Appendix C. Brain delta JSONL` headings
- `brain_delta` emitted as a JSON array instead of JSONL lines
- source/event ids referenced by records but missing from ledger blocks
- non-ISO values such as `available_from: after_outcome_snapshot`
- semantic PASS aliases such as `audit_status`, `semantic_gate_status`, `semantic_entailment`, and `pass: true`

Dry-run result after patching:

```text
total_files: 22
validation_failures: 0
unknown_typed_payload: 0
missing_source_reference: 0
total_records: 6148
total_training_eligible_records: 3944
zero_record_files: 1
```

The only true zero-record file was `20230907_nslab_episode_bundle.md`. It contains a `brain_delta_record_count` and hash summary, but no actual `brain_delta.jsonl` payload. Repair must not invent records from that.

## File results

| file | records | training_eligible | validation | note |
|---|---:|---:|---|---|
| 20180103_nslab_episode_bundle.md | 279 | 208 | pass | repaired |
| 20180104_nslab_episode_bundle.md | 97 | 97 | pass | repaired |
| 20180105_nslab_episode_bundle.md | 387 | 50 | pass | repaired |
| 20180108_nslab_episode_bundle.md | 604 | 503 | pass | repaired |
| 20180109_nslab_episode_bundle.md | 109 | 78 | pass | event placeholder repaired |
| 20180110_nslab_episode_bundle.md | 138 | 82 | pass | repaired |
| 20201030_nslab_episode_bundle.md | 207 | 141 | pass | repaired |
| 20201102_nslab_episode_bundle.md | 612 | 428 | pass | available_from repaired |
| 20201103_nslab_episode_bundle.md | 352 | 161 | pass | repaired |
| 20201104_nslab_episode_bundle.md | 166 | 52 | pass | repaired |
| 20201105_nslab_episode_bundle.md | 200 | 86 | pass | available_from repaired |
| 20201106_nslab_episode_bundle.md | 307 | 226 | pass | ARTIFACT heading repaired |
| 20201109_nslab_episode_bundle.md | 602 | 296 | pass | repaired |
| 20230829_nslab_episode_bundle.md | 186 | 70 | pass | repaired |
| 20230830_nslab_episode_bundle.md | 475 | 355 | pass | repaired |
| 20230831_nslab_episode_bundle.md | 281 | 185 | pass | source placeholder repaired |
| 20230901_nslab_episode_bundle.md | 164 | 62 | pass | repaired |
| 20230904_nslab_episode_bundle.md | 553 | 550 | pass | type alias/source placeholder repaired |
| 20230905_nslab_episode_bundle.md | 180 | 72 | pass | type alias repaired |
| 20230906_nslab_episode_bundle.md | 223 | 217 | pass | type alias repaired |
| 20230907_nslab_episode_bundle.md | 0 | 0 | pass | original payload absent |
| 20230908_nslab_episode_bundle.md | 26 | 25 | pass | JSON array appendix repaired |

## Implementation summary

`versioned_bundle.py` now accepts more valid artifact wrappers:

- `BEGIN_ARTIFACT`
- `NSLAB_BLOCK_START`
- heading + fenced JSON/JSONL
- `ARTIFACT:` heading prefixes
- `Machine Appendix C. Brain delta JSONL`
- JSON array payloads for `.jsonl` artifacts

`repair_research_bundle.py` now performs flexible packaging repair:

- maps observed record type aliases to canonical brain record types
- collects source/fact/inference ids from both top-level fields and nested payloads
- creates source/event placeholder ledger rows only for ids already referenced by records
- does not invent source/event content
- replaces non-ISO `available_from` state labels with bundle default datetimes
- normalizes semantic PASS aliases into standard PASS fields

## Operational rule

Do not treat `training eligible = 0` as immediate research failure.

First check whether the original Markdown actually contains `brain_delta` payload records. If payload records exist, the likely issue is parser/repair flexibility. If payload records are absent, as in `20230907`, the research output must be regenerated rather than repaired.

## Verification

```text
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest -q
```

All three gates passed on the final worktree.