"""Parser for single-file NSLAB Markdown research bundles."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from news_scalping_lab.contracts.models import Provenance, ResearchEpisode
from news_scalping_lab.utils import canonical_json, file_sha256, now_kst, sha256_text, stable_id

REQUIRED_BUNDLE_BLOCKS = {
    "research_report.md",
    "blind_prediction.json",
    "research_episode.json",
    "brain_delta.jsonl",
    "source_ledger.jsonl",
    "bundle_manifest.json",
}


class BundleImportError(ValueError):
    """Raised when a Markdown bundle is present but structurally invalid."""


@dataclass(frozen=True)
class BundleParseResult:
    blocks: dict[str, str]
    json_blocks: dict[str, Any]
    jsonl_blocks: dict[str, list[dict[str, Any]]]
    validation: dict[str, bool]


def looks_like_bundle(path: Path) -> bool:
    if path.suffix.lower() not in {".md", ".markdown"}:
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return "<!-- NSLAB:BEGIN " in text and "<!-- NSLAB:END " in text


def import_bundle_episode(path: Path) -> ResearchEpisode:
    parsed = parse_bundle(path)
    if "research_episode.json" not in parsed.json_blocks:
        raise BundleImportError("bundle is missing research_episode.json")
    try:
        episode = ResearchEpisode.model_validate(parsed.json_blocks["research_episode.json"])
    except ValidationError as exc:
        raise BundleImportError(f"research_episode.json failed schema validation: {exc}") from exc

    source_hash = file_sha256(path)
    provenance = Provenance(
        source_id=stable_id("SRC", path.as_posix(), source_hash),
        source_type="nslab_markdown_bundle",
        uri=path.as_posix(),
        content_sha256=source_hash,
        observed_at=now_kst(),
    )
    return episode.model_copy(update={"provenance": [*episode.provenance, provenance]})


def parse_bundle(path: Path) -> BundleParseResult:
    text = path.read_text(encoding="utf-8", errors="replace")
    blocks = _extract_blocks(text)
    missing = sorted(REQUIRED_BUNDLE_BLOCKS - set(blocks))
    if missing:
        raise BundleImportError(f"bundle missing required blocks: {', '.join(missing)}")

    json_blocks: dict[str, Any] = {}
    jsonl_blocks: dict[str, list[dict[str, Any]]] = {}
    for name, block in blocks.items():
        payload = _strip_optional_fence(block)
        if name.endswith(".json"):
            json_blocks[name] = _parse_json(name, payload)
        elif name.endswith(".jsonl"):
            jsonl_blocks[name] = _parse_jsonl(name, payload)

    _validate_jsonl_contracts(jsonl_blocks)
    validation = {
        "markers_complete": True,
        "json_valid": True,
        "jsonl_valid": True,
        "blind_hash_verified": _verify_blind_hash(json_blocks),
    }
    return BundleParseResult(
        blocks=blocks,
        json_blocks=json_blocks,
        jsonl_blocks=jsonl_blocks,
        validation=validation,
    )


def _extract_blocks(text: str) -> dict[str, str]:
    blocks: dict[str, str] = {}
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("<!-- NSLAB:BEGIN ") and stripped.endswith(" -->"):
            name = stripped.removeprefix("<!-- NSLAB:BEGIN ").removesuffix(" -->").strip()
            if name in blocks:
                raise BundleImportError(f"duplicate BEGIN block: {name}")
            end_marker = f"<!-- NSLAB:END {name} -->"
            index += 1
            content: list[str] = []
            found_end = False
            while index < len(lines):
                if lines[index].strip() == end_marker:
                    found_end = True
                    break
                content.append(lines[index])
                index += 1
            if not found_end:
                raise BundleImportError(f"missing END marker for block: {name}")
            blocks[name] = "\n".join(content).strip()
        index += 1
    return blocks


def _strip_optional_fence(block: str) -> str:
    lines = block.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return block.strip()


def _parse_json(name: str, payload: str) -> Any:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise BundleImportError(f"{name} is not valid JSON: {exc}") from exc


def _parse_jsonl(name: str, payload: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BundleImportError(f"{name}:{line_number} is not valid JSONL: {exc}") from exc
        if not isinstance(parsed, dict):
            raise BundleImportError(f"{name}:{line_number} must be a JSON object")
        rows.append(parsed)
    return rows


def _validate_jsonl_contracts(jsonl_blocks: dict[str, list[dict[str, Any]]]) -> None:
    for index, row in enumerate(jsonl_blocks.get("brain_delta.jsonl", []), start=1):
        if "record_type" not in row:
            raise BundleImportError(f"brain_delta.jsonl:{index} missing record_type")
    for index, row in enumerate(jsonl_blocks.get("source_ledger.jsonl", []), start=1):
        if "source_id" not in row:
            raise BundleImportError(f"source_ledger.jsonl:{index} missing source_id")


def _verify_blind_hash(json_blocks: dict[str, Any]) -> bool:
    blind = json_blocks.get("blind_prediction.json")
    manifest = json_blocks.get("bundle_manifest.json", {})
    if not isinstance(blind, dict) or not isinstance(manifest, dict):
        return False
    expected = manifest.get("blind_artifact_sha256")
    if not isinstance(expected, str) or not expected:
        return False
    candidate = dict(blind)
    observed = candidate.get("blind_artifact_sha256")
    if observed == expected:
        return True
    candidate["blind_artifact_sha256"] = None
    return sha256_text(canonical_json(candidate)) == expected
