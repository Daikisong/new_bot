from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from typer.testing import CliRunner

import news_scalping_lab.cli as cli_module
from news_scalping_lab.cli import app
from news_scalping_lab.config import Settings


class _AnalysisResult:
    def __init__(self, *, mode: str) -> None:
        self.mode = mode

    def model_dump(self, *, mode: str = "json") -> dict[str, str]:
        return {"mode": self.mode, "dump_mode": mode}


def test_analyze_cli_uses_configured_default_mode_when_mode_is_omitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured_modes: list[str] = []

    class CapturingAnalyzer:
        def __init__(self, settings: Settings) -> None:
            self.settings = settings

        async def analyze(self, **kwargs: Any) -> _AnalysisResult:
            selected_mode = kwargs["mode"]
            captured_modes.append(selected_mode)
            return _AnalysisResult(mode=selected_mode)

    monkeypatch.setattr(
        cli_module,
        "load_settings",
        lambda: Settings(project_root=tmp_path, default_mode="fast"),
    )
    monkeypatch.setattr(cli_module, "DailyAnalyzer", CapturingAnalyzer)

    result = CliRunner().invoke(
        app,
        [
            "analyze",
            "--news",
            str(tmp_path / "news.csv"),
            "--trade-date",
            "2030-01-10",
            "--cutoff",
            "2030-01-10T08:59:59+09:00",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured_modes == ["fast"]
    assert json.loads(result.output)["mode"] == "fast"
