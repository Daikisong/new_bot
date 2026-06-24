"""Launch helpers for the optional Streamlit UI."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StreamlitLaunchConfig:
    host: str = "127.0.0.1"
    port: int = 8501
    headless: bool = False


class StreamlitLaunchError(RuntimeError):
    """Raised when the optional UI cannot be launched."""


def streamlit_app_path() -> Path:
    return Path(__file__).with_name("app.py")


def build_streamlit_command(config: StreamlitLaunchConfig) -> list[str]:
    if not config.host.strip():
        raise ValueError("host must not be empty")
    if config.port < 1 or config.port > 65_535:
        raise ValueError("port must be between 1 and 65535")
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(streamlit_app_path()),
        "--server.address",
        config.host,
        "--server.port",
        str(config.port),
        "--server.headless",
        "true" if config.headless else "false",
    ]


def run_streamlit_ui(
    config: StreamlitLaunchConfig,
    *,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]] = subprocess.run,
) -> int:
    if importlib.util.find_spec("streamlit") is None:
        raise StreamlitLaunchError(
            "Streamlit is not installed. Run `python -m pip install -e \".[ui]\"` first."
        )
    return runner(build_streamlit_command(config)).returncode
