# Repository Rules

- This repository is an LLM-native news scalping research system.
- Do not hardcode stocks, tickers, themes, regions, or beneficiary mappings in production code.
- Store research knowledge in `research/`, `memory/`, and `brain/`, not in source code.
- Exact keyword retrieval is only supporting evidence, never a judgment gate.
- Candidate generation always starts with an open-world pass.
- New research must be incorporated without source-code changes.
- Blind inference must not access D-day prices or information after the cutoff.
- Every output must include provenance and a context manifest.
- Completion requires `ruff`, `mypy`, and `pytest` to pass.

Record rules: brain_delta is a first-class source of truth and must never be
discarded. Production brain uses llm-full mode; catalog/full/incremental outputs
are not production brains. Production semantic indexes must use a real embedding
provider; deterministic vector indexes are local/test artifacts only. Episode
coverage is not record coverage. Training exports must originate from explicit
eligible records when `memory/records/` exists. Unknown bundle versions or record
types must be preserved or quarantined, never silently dropped.
Company memory derived from `company_memory_delta` records must respect both
`known_at` and `available_from`; never backfill future relationships into a
historical analysis context.

Test commands:

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```
