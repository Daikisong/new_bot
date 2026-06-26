"""Small helpers for machine and human-readable diagnostic reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from news_scalping_lab.utils import write_json


def write_diagnostic_report(root: Path, name: str, payload: dict[str, Any]) -> dict[str, str]:
    diagnostics_dir = root / "diagnostics"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    json_path = diagnostics_dir / f"{name}.json"
    md_path = diagnostics_dir / f"{name}.md"
    write_json(json_path, payload)
    md_path.write_text(_markdown_report(name, payload), encoding="utf-8")
    return {
        "json": json_path.relative_to(root).as_posix(),
        "markdown": md_path.relative_to(root).as_posix(),
    }


def _markdown_report(name: str, payload: dict[str, Any]) -> str:
    title = name.replace("_", " ").title()
    lines = [f"# {title}", ""]
    for key, value in payload.items():
        if isinstance(value, dict):
            lines.extend([f"## {key}", ""])
            for nested_key, nested_value in value.items():
                lines.append(f"- {nested_key}: `{nested_value}`")
            lines.append("")
        elif isinstance(value, list):
            lines.extend([f"## {key}", ""])
            if not value:
                lines.append("- none")
            else:
                lines.extend(f"- `{item}`" for item in value[:200])
            lines.append("")
        else:
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).rstrip() + "\n"
