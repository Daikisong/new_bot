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

Test commands:

```bash
python -m ruff check .
python -m mypy src/news_scalping_lab
python -m pytest
```
