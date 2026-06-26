.PHONY: install-dev doctor test lint typecheck check audit full-check demo

install-dev:
	python -m pip install -e ".[dev]"

doctor:
	python -m news_scalping_lab.cli doctor

test:
	python -m pytest

lint:
	python -m ruff check .

typecheck:
	python -m mypy src/news_scalping_lab

check: lint typecheck test

audit:
	python -m news_scalping_lab.cli audit hardcoding
	python -m news_scalping_lab.cli audit provenance
	python -m news_scalping_lab.cli audit lookahead --trade-date 2026-06-24
	python -m news_scalping_lab.cli audit coverage
	python -m news_scalping_lab.cli brain audit

full-check:
	python -m news_scalping_lab.cli full-check

demo:
	python -m news_scalping_lab.cli demo
