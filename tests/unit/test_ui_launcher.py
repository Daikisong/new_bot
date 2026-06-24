from __future__ import annotations

import sys

import pytest

from news_scalping_lab.ui.launcher import StreamlitLaunchConfig, build_streamlit_command


def test_build_streamlit_command_targets_local_ui_app() -> None:
    command = build_streamlit_command(
        StreamlitLaunchConfig(host="0.0.0.0", port=8601, headless=True)
    )

    assert command[:4] == [sys.executable, "-m", "streamlit", "run"]
    assert command[4].endswith("news_scalping_lab\\ui\\app.py") or command[4].endswith(
        "news_scalping_lab/ui/app.py"
    )
    assert command[-6:] == [
        "--server.address",
        "0.0.0.0",
        "--server.port",
        "8601",
        "--server.headless",
        "true",
    ]


def test_build_streamlit_command_rejects_invalid_config() -> None:
    with pytest.raises(ValueError, match="host must not be empty"):
        build_streamlit_command(StreamlitLaunchConfig(host=" ", port=8501))
    with pytest.raises(ValueError, match="port must be between 1 and 65535"):
        build_streamlit_command(StreamlitLaunchConfig(host="127.0.0.1", port=0))
