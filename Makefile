.PHONY: install-dev doctor test lint typecheck check audit full-check demo

PYTHON ?= $(shell if command -v python.exe >/dev/null 2>&1; then printf 'python.exe'; else printf 'python'; fi)

install-dev:
	$(PYTHON) -m pip install -e ".[dev]"

doctor:
	$(PYTHON) -m news_scalping_lab.cli doctor

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .

typecheck:
	$(PYTHON) -m mypy src/news_scalping_lab

check: lint typecheck test

audit:
	$(PYTHON) -m news_scalping_lab.cli audit hardcoding
	$(PYTHON) -m news_scalping_lab.cli audit provenance
	$(PYTHON) -m news_scalping_lab.cli audit lookahead --trade-date 2026-06-24
	$(PYTHON) -m news_scalping_lab.cli audit coverage
	$(PYTHON) -m news_scalping_lab.cli brain audit

full-check:
	$(PYTHON) -m news_scalping_lab.cli full-check

demo:
	$(PYTHON) -m news_scalping_lab.cli demo
