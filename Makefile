.PHONY: install-dev doctor test lint typecheck check demo

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

demo:
	python -m news_scalping_lab.cli init
	python -m news_scalping_lab.cli news inspect docs/csv/news_20260624.csv
	python -m news_scalping_lab.cli brain rebuild --mode full
	python -m news_scalping_lab.cli analyze --news docs/csv/news_20260624.csv --trade-date 2026-06-24 --cutoff 2026-06-24T08:59:59+09:00 --mode exhaustive
