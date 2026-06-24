"""Brain snapshot diff generation."""

from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import cast

from news_scalping_lab.utils import file_sha256, now_kst, read_json


def build_brain_diff(root: Path, version_a: str, version_b: str) -> dict[str, object]:
    label_a, dir_a = _resolve_brain_dir(root, version_a)
    label_b, dir_b = _resolve_brain_dir(root, version_b)
    manifest_a = _read_json_object(dir_a / "brain_manifest.json")
    manifest_b = _read_json_object(dir_b / "brain_manifest.json")
    coverage_a = _read_json_object(dir_a / "coverage_manifest.json")
    coverage_b = _read_json_object(dir_b / "coverage_manifest.json")
    file_changes = _file_changes(dir_a, dir_b)
    added_episode_ids, removed_episode_ids = _set_delta(
        _string_list(coverage_a.get("covered_episode_ids")),
        _string_list(coverage_b.get("covered_episode_ids")),
    )
    added_claim_ids, removed_claim_ids = _set_delta(
        _string_list(manifest_a.get("claim_ids")),
        _string_list(manifest_b.get("claim_ids")),
    )
    source_hash_changes = _mapping_changes(
        _string_mapping(manifest_a.get("source_hashes")),
        _string_mapping(manifest_b.get("source_hashes")),
    )
    return {
        "version_a": label_a,
        "version_b": label_b,
        "generated_at": now_kst().isoformat(),
        "changed": bool(
            file_changes
            or added_episode_ids
            or removed_episode_ids
            or added_claim_ids
            or removed_claim_ids
            or source_hash_changes
        ),
        "file_change_counts": _file_change_counts(file_changes),
        "file_changes": file_changes,
        "added_episode_ids": added_episode_ids,
        "removed_episode_ids": removed_episode_ids,
        "added_claim_ids": added_claim_ids,
        "removed_claim_ids": removed_claim_ids,
        "source_hash_changes": source_hash_changes,
    }


def write_brain_diff_markdown(
    root: Path, diff: dict[str, object], *, output_name: str | None = None
) -> Path:
    diffs_dir = root / "brain" / "diffs"
    diffs_dir.mkdir(parents=True, exist_ok=True)
    version_a = str(diff["version_a"])
    version_b = str(diff["version_b"])
    filename = output_name or f"{_safe_filename(version_a)}_to_{_safe_filename(version_b)}.md"
    path = diffs_dir / filename
    path.write_text(render_brain_diff_markdown(diff), encoding="utf-8")
    return path


def write_brain_diff(root: Path, version_a: str, version_b: str, *, output_name: str | None = None) -> Path:
    diff = build_brain_diff(root, version_a, version_b)
    return write_brain_diff_markdown(root, diff, output_name=output_name)


def write_rebuild_diff(root: Path, previous_version: str | None, new_version: str) -> Path:
    output_name = f"{_safe_filename(new_version)}.md"
    if previous_version is None:
        diff = {
            "version_a": "none",
            "version_b": new_version,
            "generated_at": now_kst().isoformat(),
            "changed": True,
            "file_change_counts": {"added": 0, "removed": 0, "changed": 0, "unchanged": 0},
            "file_changes": [],
            "added_episode_ids": [],
            "removed_episode_ids": [],
            "added_claim_ids": [],
            "removed_claim_ids": [],
            "source_hash_changes": [],
        }
        return write_brain_diff_markdown(root, diff, output_name=output_name)
    return write_brain_diff(root, previous_version, new_version, output_name=output_name)


def render_brain_diff_markdown(diff: dict[str, object]) -> str:
    file_changes = cast(list[dict[str, object]], diff["file_changes"])
    source_hash_changes = cast(list[dict[str, object]], diff["source_hash_changes"])
    lines = [
        f"# Brain Diff {diff['version_a']} -> {diff['version_b']}",
        "",
        f"Generated at: {diff['generated_at']}",
        f"Changed: {diff['changed']}",
        "",
        "## Episode Coverage",
        "",
        _bullet_list("Added", cast(list[str], diff["added_episode_ids"])),
        _bullet_list("Removed", cast(list[str], diff["removed_episode_ids"])),
        "",
        "## Claims",
        "",
        _bullet_list("Added", cast(list[str], diff["added_claim_ids"])),
        _bullet_list("Removed", cast(list[str], diff["removed_claim_ids"])),
        "",
        "## Source Hashes",
        "",
    ]
    if source_hash_changes:
        for item in source_hash_changes:
            lines.append(
                f"- {item['key']}: {item['status']} "
                f"({item.get('old_sha256') or '-'} -> {item.get('new_sha256') or '-'})"
            )
    else:
        lines.append("- No source hash changes.")
    lines.extend(["", "## Files", ""])
    if not file_changes:
        lines.append("- No file content changes.")
    for change in file_changes:
        lines.extend(
            [
                f"### {change['file']}",
                "",
                f"- Status: {change['status']}",
                f"- Old SHA256: {change.get('old_sha256') or '-'}",
                f"- New SHA256: {change.get('new_sha256') or '-'}",
                "",
            ]
        )
        line_diff = cast(list[str], change.get("line_diff", []))
        if line_diff:
            lines.append("```diff")
            lines.extend(line_diff)
            lines.append("```")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _file_changes(dir_a: Path, dir_b: Path) -> list[dict[str, object]]:
    names = sorted(
        {path.name for path in dir_a.iterdir() if path.is_file()}
        | {path.name for path in dir_b.iterdir() if path.is_file()}
    )
    changes: list[dict[str, object]] = []
    for name in names:
        path_a = dir_a / name
        path_b = dir_b / name
        old_hash = file_sha256(path_a) if path_a.exists() else None
        new_hash = file_sha256(path_b) if path_b.exists() else None
        status = _file_status(path_a.exists(), path_b.exists(), old_hash, new_hash)
        if status == "unchanged":
            continue
        entry: dict[str, object] = {
            "file": name,
            "status": status,
            "old_sha256": old_hash,
            "new_sha256": new_hash,
        }
        if path_a.exists() and path_b.exists():
            entry["line_diff"] = _unified_diff(path_a, path_b)
        changes.append(entry)
    return changes


def _file_status(old_exists: bool, new_exists: bool, old_hash: str | None, new_hash: str | None) -> str:
    if old_exists and not new_exists:
        return "removed"
    if new_exists and not old_exists:
        return "added"
    if old_hash != new_hash:
        return "changed"
    return "unchanged"


def _unified_diff(path_a: Path, path_b: Path) -> list[str]:
    old_lines = path_a.read_text(encoding="utf-8").splitlines()
    new_lines = path_b.read_text(encoding="utf-8").splitlines()
    return list(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=path_a.name,
            tofile=path_b.name,
            lineterm="",
        )
    )


def _file_change_counts(file_changes: list[dict[str, object]]) -> dict[str, int]:
    counts = {"added": 0, "removed": 0, "changed": 0, "unchanged": 0}
    for change in file_changes:
        status = str(change["status"])
        counts[status] = counts.get(status, 0) + 1
    return counts


def _resolve_brain_dir(root: Path, version: str) -> tuple[str, Path]:
    normalized = version.strip()
    if normalized.lower() == "head":
        normalized = _current_brain_version(root) or ""
    if normalized.lower() == "current":
        path = root / "brain" / "current"
        if path.exists():
            return "current", path
    path = root / "brain" / "snapshots" / normalized
    if normalized and path.exists():
        return normalized, path
    raise FileNotFoundError(f"brain snapshot not found: {version}")


def _current_brain_version(root: Path) -> str | None:
    head = root / "brain" / "HEAD"
    if not head.exists():
        return None
    value = head.read_text(encoding="utf-8").strip()
    return value or None


def _read_json_object(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    value = read_json(path)
    if isinstance(value, dict):
        return cast(dict[str, object], value)
    return {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _string_mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items() if isinstance(item, str)}


def _set_delta(old: list[str], new: list[str]) -> tuple[list[str], list[str]]:
    old_set = set(old)
    new_set = set(new)
    return sorted(new_set - old_set), sorted(old_set - new_set)


def _mapping_changes(old: dict[str, str], new: dict[str, str]) -> list[dict[str, object]]:
    changes: list[dict[str, object]] = []
    for key in sorted(set(old) | set(new)):
        old_value = old.get(key)
        new_value = new.get(key)
        if old_value == new_value:
            continue
        changes.append(
            {
                "key": key,
                "status": _mapping_status(key, old, new),
                "old_sha256": old_value,
                "new_sha256": new_value,
            }
        )
    return changes


def _mapping_status(key: str, old: dict[str, str], new: dict[str, str]) -> str:
    if key not in old:
        return "added"
    if key not in new:
        return "removed"
    return "changed"


def _bullet_list(label: str, values: list[str]) -> str:
    if not values:
        return f"- {label}: none"
    return f"- {label}: {', '.join(values)}"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip())
    return safe.strip("-") or "brain-diff"
