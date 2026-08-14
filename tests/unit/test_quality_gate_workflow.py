from pathlib import Path

WORKFLOW = Path(".github/workflows/quality-gate.yml")


def test_quality_gate_workflow_exists() -> None:
    assert WORKFLOW.is_file()


def test_quality_gate_check_name_is_stable() -> None:
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "name: quality-gate" in content
    assert "  quality-gate:" in content


def test_quality_gate_has_no_continue_on_error() -> None:
    content = WORKFLOW.read_text(encoding="utf-8")
    assert "continue-on-error" not in content
    assert "python -m pytest" in content
    assert "python -m ruff check ." in content
    assert "python -m mypy src/news_scalping_lab" in content
