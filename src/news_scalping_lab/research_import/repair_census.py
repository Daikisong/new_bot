"""Independent raw-byte census for research bundle repair.

This module intentionally does not call ``parse_generic_bundle``. Its job is to
detect parser blind spots by retaining every machine-looking occurrence instead
of collapsing artifacts into a name-keyed dictionary.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from news_scalping_lab.research_import.repair_models import (
    ArtifactOccurrence,
    ArtifactRow,
    SourceCensus,
    UnclaimedMachinePayload,
)
from news_scalping_lab.utils import canonical_json, sha256_bytes, sha256_text

_MARKER_PATTERNS = (
    (
        "INPUT_COVERAGE_RECEIPT",
        re.compile(
            r"<!--\s*NSLAB:INPUT_COVERAGE_RECEIPT_BEGIN\s*-->"
            r"(?P<payload>.*?)"
            r"<!--\s*NSLAB:INPUT_COVERAGE_RECEIPT_END\s*-->",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    (
        "NSLAB_ARTIFACT",
        re.compile(
            r"<!--\s*NSLAB_ARTIFACT_BEGIN\s+(?P<name>[^>]+?)\s*-->"
            r"(?P<payload>.*?)"
            r"<!--\s*NSLAB_ARTIFACT_END\s+(?P=name)\s*-->",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    (
        "NSLAB_MARKER",
        re.compile(
            r"<!--\s*NSLAB:BEGIN\s+(?P<name>[^>]+?)\s*-->"
            r"(?P<payload>.*?)"
            r"<!--\s*NSLAB:END\s+(?P=name)\s*-->",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    (
        "BEGIN_ARTIFACT",
        re.compile(
            r"<!--\s*BEGIN_ARTIFACT\s+(?P<name>[^>]+?)\s*-->"
            r"(?P<payload>.*?)"
            r"<!--\s*END_ARTIFACT\s+(?P=name)\s*-->",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
    (
        "NSLAB_BLOCK",
        re.compile(
            r"<!--\s*NSLAB_BLOCK_START:\s*(?P<name>[^>]+?)\s*-->"
            r"(?P<payload>.*?)"
            r"<!--\s*NSLAB_BLOCK_END:\s*(?P=name)\s*-->",
            re.DOTALL | re.IGNORECASE,
        ),
    ),
)

_HEADING_NAME = re.compile(
    r"^#{1,6}\s+(?:(?:ARTIFACT|ARTIFACTS|ARTIFACT_PAYLOAD):\s*)?"
    r"`?([A-Za-z0-9_.\-/]+(?:\.jsonl|\.json|\.md))`?\s*$",
    re.IGNORECASE,
)
_OPENING_FENCE = re.compile(r"^(?P<fence>`{3,}|~{3,})(?P<rest>.*)$")
_UNKNOWN_ARTIFACT_START = re.compile(
    r"<!--(?P<header>[^>]*(?:BEGIN|START)[^>]*\.(?:jsonl|json)[^>]*)-->",
    re.IGNORECASE,
)
_UNKNOWN_ARTIFACT_END = re.compile(
    r"<!--(?P<header>[^>]*(?:END|STOP)[^>]*\.(?:jsonl|json)[^>]*)-->",
    re.IGNORECASE,
)
_ARTIFACT_NAME_TOKEN = re.compile(
    r"[A-Za-z0-9_.\-/\\]+\.(?:jsonl|json)",
    re.IGNORECASE,
)
_MACHINE_KEYS = {
    "artifact_type",
    "brain_delta_id",
    "candidate_id",
    "episode_id",
    "fact_id",
    "inference_id",
    "outcome_id",
    "outcome_row_id",
    "record_id",
    "record_type",
    "schema_version",
    "screening_id",
    "source_id",
}
_MACHINE_KEY_TOKEN = re.compile(
    rf"(?i)[\"'](?:{'|'.join(sorted(_MACHINE_KEYS))})[\"']\s*:",
)
_RECORD_TYPE_TOKEN = re.compile(r"(?i)[\"']record_type[\"']\s*:")

# Declared code/text artifacts are evidence carried by a research bundle, not
# machine-readable ledgers.  A code snippet can contain JSON-looking literals
# (for example ``print({"record_id": ...})``); treating that as an unclaimed
# machine payload would create a false adapter blocker.  Keep this allowlist
# extension-based and generic so new artifact filenames do not need patches.
_OPAQUE_TEXT_EXTENSIONS = frozenset(
    {
        ".bash",
        ".bat",
        ".cfg",
        ".cmd",
        ".conf",
        ".css",
        ".csv",
        ".go",
        ".htm",
        ".html",
        ".ini",
        ".java",
        ".js",
        ".jsx",
        ".log",
        ".ps1",
        ".py",
        ".rs",
        ".sass",
        ".scss",
        ".sh",
        ".sql",
        ".svg",
        ".swift",
        ".tex",
        ".ts",
        ".tsx",
        ".txt",
        ".xml",
        ".yaml",
        ".yml",
    }
)


@dataclass(frozen=True)
class _RawOccurrence:
    raw_name: str | None
    canonical_name: str | None
    wrapper_kind: str
    char_start: int
    char_end: int
    payload_char_start: int
    payload_char_end: int
    payload: str
    declared_format: str | None
    overlapping_alias: bool = False


@dataclass(frozen=True)
class _FenceSpan:
    char_start: int
    char_end: int
    payload_char_start: int
    payload_char_end: int
    language: str | None
    closed: bool


@dataclass(frozen=True)
class _UnclaimedCandidate:
    char_start: int
    char_end: int
    detected_shape: str
    reason: str


def census_source(path: Path) -> SourceCensus:
    raw_bytes = path.read_bytes()
    source_sha256 = sha256_bytes(raw_bytes)
    try:
        text = raw_bytes.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        return SourceCensus(
            source_path=path,
            source_sha256=source_sha256,
            byte_size=len(raw_bytes),
            strict_utf8_ok=False,
            decode_error=str(exc),
            structure_fingerprint=sha256_text("INVALID_UTF8"),
        )

    raw_occurrences = _marker_occurrences(text)
    raw_occurrences.extend(_heading_occurrences(text))
    raw_occurrences = _mark_overlapping_aliases(raw_occurrences)
    byte_offsets = _byte_offsets(
        text,
        {
            position
            for occurrence in raw_occurrences
            for position in (occurrence.char_start, occurrence.char_end)
        },
        bom_bytes=3 if raw_bytes.startswith(b"\xef\xbb\xbf") else 0,
    )

    occurrences: list[ArtifactOccurrence] = []
    artifact_counts: Counter[str] = Counter()
    explicit_record_count = 0
    for ordinal, raw in enumerate(raw_occurrences, start=1):
        parsed = _parse_payload(raw.canonical_name, raw.payload, raw.declared_format)
        byte_start = byte_offsets[raw.char_start]
        byte_end = byte_offsets[raw.char_end]
        occurrence_seed = (
            f"{source_sha256}:{raw.canonical_name or 'UNNAMED'}:{ordinal}:"
            f"{byte_start}:{byte_end}"
        )
        occurrence = ArtifactOccurrence(
            occurrence_id=f"OCC-{sha256_text(occurrence_seed)[:24]}",
            raw_name=raw.raw_name,
            canonical_name=raw.canonical_name,
            wrapper_kind=raw.wrapper_kind,
            byte_start=byte_start,
            byte_end=byte_end,
            payload_sha256=sha256_text(raw.payload),
            canonical_payload_sha256=parsed["canonical_payload_sha256"],
            declared_format=raw.declared_format,
            parse_status=parsed["parse_status"],
            row_count=parsed["row_count"],
            top_level_shape=parsed["top_level_shape"],
            explicit_record_ids=parsed["explicit_record_ids"],
            overlapping_alias=raw.overlapping_alias,
            error=parsed["error"],
        )
        occurrences.append(occurrence)
        if raw.canonical_name is not None and not raw.overlapping_alias:
            artifact_counts[raw.canonical_name] += 1
        if (
            raw.canonical_name == "brain_delta.jsonl"
            and parsed["row_count"] is not None
            and not raw.overlapping_alias
        ):
            explicit_record_count += int(parsed["row_count"])

    unclaimed = _unclaimed_machine_payloads(
        text,
        raw_bytes=raw_bytes,
        source_sha256=source_sha256,
        claimed=raw_occurrences,
        bom_bytes=3 if raw_bytes.startswith(b"\xef\xbb\xbf") else 0,
    )
    duplicate_names, conflicting_names = _duplicate_names(occurrences)
    fingerprint_payload = [
        (
            occurrence.wrapper_kind,
            occurrence.canonical_name,
            occurrence.declared_format,
            occurrence.top_level_shape,
            occurrence.parse_status,
            _payload_field_signature(raw.payload, raw.declared_format),
        )
        for occurrence, raw in zip(occurrences, raw_occurrences, strict=True)
        if not occurrence.overlapping_alias
    ]
    return SourceCensus(
        source_path=path,
        source_sha256=source_sha256,
        byte_size=len(raw_bytes),
        strict_utf8_ok=True,
        replacement_character_count=text.count("\ufffd"),
        artifact_occurrences=occurrences,
        unclaimed_machine_payloads=unclaimed,
        artifact_counts=dict(sorted(artifact_counts.items())),
        duplicate_names=duplicate_names,
        conflicting_duplicate_names=conflicting_names,
        explicit_record_count=explicit_record_count,
        raw_record_type_token_count=len(_RECORD_TYPE_TOKEN.findall(text)),
        structure_fingerprint=sha256_text(canonical_json(fingerprint_payload)),
    )


def artifact_rows(path: Path) -> list[ArtifactRow]:
    """Return every independently discovered JSON/JSONL row with a stable origin."""

    raw_bytes = path.read_bytes()
    source_sha256 = sha256_bytes(raw_bytes)
    text = raw_bytes.decode("utf-8-sig", errors="strict")
    raw_occurrences = _marker_occurrences(text)
    raw_occurrences.extend(_heading_occurrences(text))
    raw_occurrences = _mark_overlapping_aliases(raw_occurrences)
    staged: list[tuple[int, _RawOccurrence, int, dict[str, Any], int, int]] = []
    positions = {
        position
        for occurrence in raw_occurrences
        for position in (occurrence.char_start, occurrence.char_end)
    }
    for occurrence_ordinal, raw in enumerate(raw_occurrences, start=1):
        if (
            raw.overlapping_alias
            or raw.canonical_name is None
            or raw.declared_format not in {"json", "jsonl"}
        ):
            continue
        container = text[raw.payload_char_start : raw.payload_char_end]
        cursor = 0
        try:
            fragments = _payload_row_fragments(raw.payload, raw.declared_format)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        for row_ordinal, (row, fragment) in enumerate(fragments, start=1):
            relative_start = container.find(fragment, cursor)
            if relative_start < 0:
                relative_start = 0
                fragment = container
            relative_end = relative_start + len(fragment)
            cursor = relative_end
            row_char_start = raw.payload_char_start + relative_start
            row_char_end = raw.payload_char_start + relative_end
            positions.update((row_char_start, row_char_end))
            staged.append(
                (
                    occurrence_ordinal,
                    raw,
                    row_ordinal,
                    row,
                    row_char_start,
                    row_char_end,
                )
            )
    offsets = _byte_offsets(
        text,
        positions,
        bom_bytes=3 if raw_bytes.startswith(b"\xef\xbb\xbf") else 0,
    )
    rows: list[ArtifactRow] = []
    for (
        occurrence_ordinal,
        raw,
        row_ordinal,
        row,
        row_char_start,
        row_char_end,
    ) in staged:
        byte_start = offsets[raw.char_start]
        byte_end = offsets[raw.char_end]
        occurrence_seed = (
            f"{source_sha256}:{raw.canonical_name}:{occurrence_ordinal}:"
            f"{byte_start}:{byte_end}"
        )
        occurrence_id = f"OCC-{sha256_text(occurrence_seed)[:24]}"
        canonical_row_sha256 = sha256_text(canonical_json(row))
        row_byte_start = offsets[row_char_start]
        row_byte_end = offsets[row_char_end]
        raw_row_bytes_sha256 = sha256_bytes(raw_bytes[row_byte_start:row_byte_end])
        origin_key = (
            f"{source_sha256}:{occurrence_id}:{row_ordinal}:"
            f"{raw_row_bytes_sha256}"
        )
        rows.append(
            ArtifactRow(
                origin_key=origin_key,
                source_sha256=source_sha256,
                occurrence_id=occurrence_id,
                canonical_name=raw.canonical_name,
                row_ordinal=row_ordinal,
                raw_payload_sha256=canonical_row_sha256,
                raw_row_byte_start=row_byte_start,
                raw_row_byte_end=row_byte_end,
                raw_row_bytes_sha256=raw_row_bytes_sha256,
                canonical_row_sha256=canonical_row_sha256,
                row=row,
            )
        )
    return rows


def _marker_occurrences(text: str) -> list[_RawOccurrence]:
    occurrences: list[_RawOccurrence] = []
    for wrapper_kind, pattern in _MARKER_PATTERNS:
        for match in pattern.finditer(text):
            raw_name = match.groupdict().get("name") or (
                "input_coverage_receipt.json"
                if wrapper_kind == "INPUT_COVERAGE_RECEIPT"
                else None
            )
            if raw_name is None:
                raise ValueError(f"artifact marker has no name: {wrapper_kind}")
            raw_name = raw_name.strip()
            payload = _strip_optional_fence(match.group("payload").strip())
            occurrences.append(
                _RawOccurrence(
                    raw_name=raw_name,
                    canonical_name=_canonical_artifact_name(raw_name),
                    wrapper_kind=wrapper_kind,
                    char_start=match.start(),
                    char_end=match.end(),
                    payload_char_start=match.start("payload"),
                    payload_char_end=match.end("payload"),
                    payload=payload,
                    declared_format=_declared_format(raw_name, None),
                )
            )
    return occurrences


def _heading_occurrences(text: str) -> list[_RawOccurrence]:
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    occurrences: list[_RawOccurrence] = []
    index = 0
    while index < len(lines):
        stripped_heading = lines[index].strip()
        # Only Markdown headings may introduce a heading-fenced artifact.  A
        # JSON string such as ``"BRAIN_DELTA"`` inside another fenced report
        # must never be promoted to an artifact heading merely because its
        # normalized alias happens to match.
        if not stripped_heading.startswith("#"):
            index += 1
            continue
        raw_name = _heading_artifact_name(stripped_heading)
        if raw_name is None:
            index += 1
            continue
        fence_index = _next_heading_fence(lines, index + 1)
        if fence_index is None:
            index += 1
            continue
        opening = _OPENING_FENCE.match(lines[fence_index].strip())
        if opening is None:
            index += 1
            continue
        fence = opening.group("fence")
        language = opening.group("rest").strip().split(" ", 1)[0]
        end_index = fence_index + 1
        while end_index < len(lines) and _closing_fence_offset(
            lines[end_index], fence
        ) is None:
            end_index += 1
        if end_index >= len(lines):
            index += 1
            continue
        closing_offset = _closing_fence_offset(lines[end_index], fence)
        payload_start = starts[fence_index] + len(lines[fence_index])
        payload_end = starts[end_index] + (closing_offset or 0)
        occurrence_end = starts[end_index] + len(lines[end_index])
        occurrences.append(
            _RawOccurrence(
                raw_name=raw_name,
                canonical_name=_canonical_artifact_name(raw_name),
                wrapper_kind="HEADING_FENCE",
                char_start=starts[index],
                char_end=occurrence_end,
                payload_char_start=payload_start,
                payload_char_end=payload_end,
                payload=text[payload_start:payload_end].strip(),
                declared_format=_declared_format(raw_name, language),
            )
        )
        index = end_index + 1
    return occurrences


def _json_front_matter_span(text: str) -> tuple[int, int] | None:
    """Return the span of a valid JSON front matter document, if present.

    A few research runners emit the front matter object as JSON between the
    opening/closing document separators instead of YAML key/value lines.  It
    is metadata, not an unwrapped artifact, but it still must parse as one
    complete object before the raw census excludes its byte span.
    """

    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    offset = len(lines[0])
    payload_lines: list[str] = []
    for line in lines[1:]:
        if line.strip() in {"---", "..."}:
            payload = "".join(payload_lines).strip()
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return None
            if not isinstance(parsed, dict):
                return None
            return 0, offset + len(line)
        payload_lines.append(line)
        offset += len(line)
    return None


def _unclaimed_machine_payloads(
    text: str,
    *,
    raw_bytes: bytes,
    source_sha256: str,
    claimed: list[_RawOccurrence],
    bom_bytes: int,
) -> list[UnclaimedMachinePayload]:
    fences = _scan_fences(text)
    candidates = _unknown_wrapper_candidates(text, claimed)
    unknown_spans = [
        (candidate.char_start, candidate.char_end) for candidate in candidates
    ]
    for fence in fences:
        if _fence_is_claimed(fence, claimed):
            continue
        if any(
            start <= fence.char_start and fence.char_end <= end
            for start, end in unknown_spans
        ):
            continue
        payload = text[fence.payload_char_start : fence.payload_char_end]
        shape, machine_like = _machine_payload_shape(payload)
        if not machine_like:
            continue
        if not fence.closed:
            reason = "unclosed machine-looking fence has no complete artifact wrapper"
        elif shape == "UNPARSEABLE":
            reason = "unparseable machine-looking fenced payload has no artifact wrapper"
        else:
            reason = "machine-looking fenced payload has no artifact wrapper"
        candidates.append(
            _UnclaimedCandidate(
                char_start=fence.char_start,
                char_end=fence.char_end,
                detected_shape=shape,
                reason=reason,
            )
        )

    excluded_spans = [
        (occurrence.payload_char_start, occurrence.payload_char_end)
        for occurrence in claimed
    ]
    excluded_spans.extend((fence.char_start, fence.char_end) for fence in fences)
    excluded_spans.extend(unknown_spans)
    front_matter_span = _json_front_matter_span(text)
    if front_matter_span is not None:
        excluded_spans.append(front_matter_span)
    candidates.extend(_bare_machine_candidates(text, excluded_spans))
    candidates = [
        candidate
        for candidate in candidates
        if not _is_exact_duplicate_of_claimed_report(
            text,
            candidate,
            claimed,
        )
    ]
    candidates.sort(key=lambda candidate: (candidate.char_start, candidate.char_end))
    positions = {
        position
        for candidate in candidates
        for position in (candidate.char_start, candidate.char_end)
    }
    offsets = _byte_offsets(text, positions, bom_bytes=bom_bytes)
    unclaimed: list[UnclaimedMachinePayload] = []
    for ordinal, candidate in enumerate(candidates, start=1):
        byte_start = offsets[candidate.char_start]
        byte_end = offsets[candidate.char_end]
        seed = f"{source_sha256}:UNCLAIMED:{ordinal}:{byte_start}:{byte_end}"
        unclaimed.append(
            UnclaimedMachinePayload(
                occurrence_id=f"UNCLAIMED-{sha256_text(seed)[:24]}",
                byte_start=byte_start,
                byte_end=byte_end,
                payload_sha256=sha256_bytes(raw_bytes[byte_start:byte_end]),
                detected_shape=candidate.detected_shape,
                reason=candidate.reason,
            )
        )
    return unclaimed


def _is_exact_duplicate_of_claimed_report(
    text: str,
    candidate: _UnclaimedCandidate,
    claimed: list[_RawOccurrence],
) -> bool:
    """Return whether a parseable narrative payload is already preserved."""

    payload = text[candidate.char_start : candidate.char_end].strip()
    shape, machine_like = _machine_payload_shape(payload)
    if not payload or not machine_like or shape == "UNPARSEABLE":
        return False
    return any(
        occurrence.canonical_name is not None
        and occurrence.canonical_name.lower().endswith(".md")
        and payload in occurrence.payload
        for occurrence in claimed
    )


def _scan_fences(text: str) -> list[_FenceSpan]:
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)

    fences: list[_FenceSpan] = []
    index = 0
    while index < len(lines):
        opening = _OPENING_FENCE.match(lines[index].strip())
        if opening is None:
            index += 1
            continue
        fence = opening.group("fence")
        rest = opening.group("rest").strip()
        language = rest.split(None, 1)[0].lower() if rest else None
        end_index = index + 1
        while end_index < len(lines) and not _is_closing_fence(
            lines[end_index],
            fence,
        ):
            end_index += 1
        closed = end_index < len(lines)
        payload_start = starts[index] + len(lines[index])
        payload_end = starts[end_index] if closed else len(text)
        char_end = starts[end_index] + len(lines[end_index]) if closed else len(text)
        fences.append(
            _FenceSpan(
                char_start=starts[index],
                char_end=char_end,
                payload_char_start=payload_start,
                payload_char_end=payload_end,
                language=language,
                closed=closed,
            )
        )
        index = end_index + 1 if closed else len(lines)
    return fences


def _unknown_wrapper_candidates(
    text: str,
    claimed: list[_RawOccurrence],
) -> list[_UnclaimedCandidate]:
    starts = list(_UNKNOWN_ARTIFACT_START.finditer(text))
    endings = list(_UNKNOWN_ARTIFACT_END.finditer(text))
    candidates: list[_UnclaimedCandidate] = []
    for index, match in enumerate(starts):
        if any(
            occurrence.char_start <= match.start() < occurrence.char_end
            for occurrence in claimed
        ):
            continue
        name = _artifact_name_from_header(match.group("header"))
        next_start = starts[index + 1].start() if index + 1 < len(starts) else len(text)
        char_end = match.end()
        for ending in endings:
            if ending.start() < match.end() or ending.end() > next_start:
                continue
            if _artifact_name_from_header(ending.group("header")) == name:
                char_end = ending.end()
                break
        candidates.append(
            _UnclaimedCandidate(
                char_start=match.start(),
                char_end=char_end,
                detected_shape="UNKNOWN_ARTIFACT_WRAPPER",
                reason="unrecognized artifact begin/start wrapper requires an adapter",
            )
        )
    return candidates


def _artifact_name_from_header(header: str) -> str | None:
    match = _ARTIFACT_NAME_TOKEN.search(header)
    return _canonical_artifact_name(match.group(0)) if match is not None else None


def _bare_machine_candidates(
    text: str,
    excluded_spans: list[tuple[int, int]],
) -> list[_UnclaimedCandidate]:
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)

    candidates: list[_UnclaimedCandidate] = []
    index = 0
    while index < len(lines):
        line_start = starts[index]
        line_end = line_start + len(lines[index])
        stripped = lines[index].strip()
        if (
            not _looks_like_bare_json_start(stripped)
            or _span_overlaps(line_start, line_end, excluded_spans)
        ):
            index += 1
            continue

        start_index = index
        end_index = index
        while True:
            char_start = starts[start_index]
            char_end = starts[end_index] + len(lines[end_index])
            payload = text[char_start:char_end].strip()
            shape, machine_like = _machine_payload_shape(payload)
            next_index = _next_nonempty_line(lines, end_index + 1)
            if next_index is None:
                break
            next_start = starts[next_index]
            next_end = next_start + len(lines[next_index])
            if _span_overlaps(next_start, next_end, excluded_spans):
                break
            next_stripped = lines[next_index].strip()
            if shape != "UNPARSEABLE":
                if not (
                    payload.lstrip().startswith("{")
                    and next_stripped.startswith("{")
                ):
                    break
            elif not _looks_like_json_continuation(next_stripped):
                break
            end_index = next_index

        char_start = starts[start_index]
        char_end = starts[end_index] + len(lines[end_index])
        payload = text[char_start:char_end].strip()
        shape, machine_like = _machine_payload_shape(payload)
        if machine_like:
            reason = (
                "unparseable bare machine-looking JSON/JSONL payload"
                if shape == "UNPARSEABLE"
                else "bare machine-looking JSON/JSONL payload has no artifact wrapper"
            )
            candidates.append(
                _UnclaimedCandidate(
                    char_start=char_start,
                    char_end=char_end,
                    detected_shape=shape,
                    reason=reason,
                )
            )
        index = end_index + 1
    return candidates


def _next_nonempty_line(lines: list[str], start: int) -> int | None:
    for index in range(start, len(lines)):
        if lines[index].strip():
            return index
    return None


def _looks_like_bare_json_start(stripped: str) -> bool:
    return stripped.startswith(("{", "["))


def _looks_like_json_continuation(stripped: str) -> bool:
    if not stripped:
        return True
    if stripped.startswith(("{", "[", "}", "]", '"', "'", ",")):
        return True
    return bool(re.match(r"^(?:-?\d|true\b|false\b|null\b)", stripped, re.IGNORECASE))


def _span_overlaps(
    char_start: int,
    char_end: int,
    spans: list[tuple[int, int]],
) -> bool:
    return any(char_start < end and start < char_end for start, end in spans)


def _fence_is_claimed(
    fence: _FenceSpan,
    claimed: list[_RawOccurrence],
) -> bool:
    return any(
        occurrence.char_start <= fence.char_start
        and fence.char_end <= occurrence.char_end
        and (
            occurrence.wrapper_kind != "HEADING_FENCE"
            or (
                occurrence.payload_char_start >= fence.payload_char_start
                and occurrence.payload_char_end <= fence.payload_char_end
            )
        )
        for occurrence in claimed
    )


def _parse_payload(
    name: str | None,
    payload: str,
    declared_format: str | None,
) -> dict[str, Any]:
    canonical_name = _canonical_artifact_name(name) if name is not None else ""
    if canonical_name.endswith(".md") or Path(canonical_name).suffix in _OPAQUE_TEXT_EXTENSIONS:
        return {
            "parse_status": "OPAQUE_TEXT",
            "row_count": None,
            "top_level_shape": "TEXT",
            "explicit_record_ids": [],
            "canonical_payload_sha256": sha256_text(payload),
            "error": None,
        }
    if declared_format not in {"json", "jsonl"}:
        shape, machine_like = _machine_payload_shape(payload)
        if machine_like:
            # Some marker wrappers omit the ``.json``/``.jsonl`` suffix even
            # though the payload is a complete JSON object or JSONL stream.
            # Infer the format from the bytes before declaring an opaque
            # machine artifact. Malformed machine-like content still returns
            # UNDECLARED_MACHINE below and remains a hard gate.
            try:
                rows, normalized_newlines = _payload_rows_with_normalization(
                    payload,
                    "json",
                )
                record_ids = [
                    str(record_id)
                    for row in rows
                    if isinstance(row, dict)
                    for record_id in (row.get("record_id") or row.get("brain_delta_id"),)
                    if isinstance(record_id, str) and record_id
                ]
                return {
                    "parse_status": (
                        "PARSED_WITH_NORMALIZED_NEWLINES"
                        if normalized_newlines
                        else "PARSED"
                    ),
                    "row_count": len(rows),
                    "top_level_shape": shape,
                    "explicit_record_ids": record_ids,
                    "canonical_payload_sha256": sha256_text(canonical_json(rows)),
                    "error": None,
                }
            except (json.JSONDecodeError, ValueError, TypeError):
                pass
        return {
            "parse_status": "UNDECLARED_MACHINE" if machine_like else "OPAQUE_TEXT",
            "row_count": None,
            "top_level_shape": shape,
            "explicit_record_ids": [],
            "canonical_payload_sha256": None,
            "error": None,
        }
    if declared_format == "json":
        try:
            parsed_json = _strict_json_loads(payload)
        except (json.JSONDecodeError, ValueError, TypeError):
            # Duplicate keys are invalid JSON and must reach the normal
            # PARSE_ERROR result below instead of escaping the source census.
            parsed_json = None
        if isinstance(parsed_json, list) and any(
            not isinstance(item, dict) for item in parsed_json
        ):
            # JSON metadata manifests such as required_blocks.json and
            # block_order.json are scalar arrays, not record collections.
            # Preserve and hash them without treating their scalar entries as
            # brain/source rows.
            return {
                "parse_status": "PARSED_METADATA",
                "row_count": None,
                "top_level_shape": "ARRAY_SCALARS",
                "explicit_record_ids": [],
                "canonical_payload_sha256": sha256_text(
                    canonical_json(parsed_json)
                ),
                "error": None,
            }
    if declared_format == "jsonl":
        metadata_values = _jsonl_metadata_values(payload)
        if metadata_values is not None:
            return {
                "parse_status": "PARSED_METADATA",
                "row_count": len(metadata_values),
                "top_level_shape": (
                    "ARRAY_SCALARS"
                    if payload.lstrip().startswith("[")
                    else "JSONL_SCALARS"
                ),
                "explicit_record_ids": [],
                "canonical_payload_sha256": sha256_text(
                    canonical_json(metadata_values)
                ),
                "error": None,
            }
    try:
        rows, normalized_newlines = _payload_rows_with_normalization(
            payload,
            declared_format,
        )
        shape = (
            "ARRAY"
            if payload.lstrip().startswith("[")
            else "OBJECT"
            if declared_format == "json"
            else "JSONL"
        )
        record_ids = [
            str(record_id)
            for row in rows
            if isinstance(row, dict)
            for record_id in (row.get("record_id") or row.get("brain_delta_id"),)
            if isinstance(record_id, str) and record_id
        ]
        return {
            "parse_status": (
                "PARSED_WITH_NORMALIZED_NEWLINES"
                if normalized_newlines
                else "PARSED"
            ),
            "row_count": len(rows),
            "top_level_shape": shape,
            "explicit_record_ids": record_ids,
            "canonical_payload_sha256": sha256_text(canonical_json(rows)),
            "error": None,
        }
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        return {
            "parse_status": "PARSE_ERROR",
            "row_count": None,
            "top_level_shape": None,
            "explicit_record_ids": [],
            "canonical_payload_sha256": None,
            "error": str(exc),
        }


def _payload_rows(payload: str, declared_format: str) -> list[dict[str, Any]]:
    rows, _ = _payload_rows_with_normalization(payload, declared_format)
    return rows


def _payload_rows_with_normalization(
    payload: str,
    declared_format: str,
) -> tuple[list[dict[str, Any]], bool]:
    if declared_format == "jsonl":
        return _parse_jsonl_with_normalization(payload)
    try:
        parsed = _strict_json_loads(payload)
    except json.JSONDecodeError:
        # Some legacy wrappers use a .json filename for a complete JSONL
        # stream. Only accept the fallback when every line is an object;
        # malformed/truncated payloads still fail the census gate.
        return _parse_jsonl_with_normalization(payload)
    if isinstance(parsed, dict):
        return [parsed], False
    if isinstance(parsed, list) and all(isinstance(row, dict) for row in parsed):
        return parsed, False
    raise ValueError("JSON artifact must contain an object or an array of objects")


def _payload_row_fragments(
    payload: str,
    declared_format: str,
) -> list[tuple[dict[str, Any], str]]:
    rows, _ = _payload_rows_with_normalization(payload, declared_format)
    stripped = payload.strip()
    effective_jsonl = declared_format == "jsonl"
    if declared_format == "json" and not stripped.startswith("["):
        try:
            _strict_json_loads(payload)
        except json.JSONDecodeError:
            effective_jsonl = True
    if effective_jsonl and not stripped.startswith("["):
        parsed_fragments = _parse_jsonl_fragments(payload)
        if len(parsed_fragments) == len(rows):
            return parsed_fragments
    return [(row, payload) for row in rows]


def _parse_jsonl(payload: str) -> list[dict[str, Any]]:
    rows, _ = _parse_jsonl_with_normalization(payload)
    return rows


def _jsonl_metadata_values(payload: str) -> list[Any] | None:
    """Return a scalar-only JSONL stream without treating its values as rows."""

    stripped = payload.strip()
    try:
        if stripped.startswith("["):
            parsed = _strict_json_loads(stripped)
            if not isinstance(parsed, list):
                return None
            values = parsed
        else:
            values = [
                _strict_json_loads(line)
                for line in payload.splitlines()
                if line.strip()
            ]
    except (json.JSONDecodeError, ValueError, TypeError):
        return None
    if not values or any(isinstance(value, dict) for value in values):
        return None
    return values


def _parse_jsonl_with_normalization(
    payload: str,
) -> tuple[list[dict[str, Any]], bool]:
    stripped = payload.strip()
    if stripped.startswith("["):
        parsed = _strict_json_loads(stripped)
        if not isinstance(parsed, list) or not all(isinstance(row, dict) for row in parsed):
            raise ValueError("JSONL array must contain objects")
        return parsed, False
    fragments = _parse_jsonl_fragments(payload)
    normalized = False
    for _, fragment in fragments:
        try:
            _strict_json_loads(fragment)
        except (json.JSONDecodeError, ValueError):
            normalized = True
            break
    return [row for row, _ in fragments], normalized


def _parse_jsonl_fragments(payload: str) -> list[tuple[dict[str, Any], str]]:
    """Parse JSONL while preserving a deterministic multiline-row repair.

    A few legacy exports contain a literal line break inside a JSON string.
    The source bytes remain the evidence; this helper only joins the physical
    lines and represents that line break as the JSON escape ``\\n``.  It never
    drops a row or accepts an incomplete object: a pending fragment must still
    parse as one complete object before it is emitted.
    """

    rows: list[tuple[dict[str, Any], str]] = []
    pending_lines: list[str] = []
    for line in payload.splitlines():
        if not line.strip():
            continue
        pending_lines.append(line)
        raw_candidate = "\n".join(pending_lines)
        candidates = (raw_candidate, "\\n".join(pending_lines))
        parsed_line: Any = None
        normalized_candidate: str | None = None
        for candidate in candidates:
            try:
                parsed_line = _strict_json_loads(candidate)
            except ValueError as exc:
                if "duplicate JSON object key" in str(exc):
                    raise
                continue
            except json.JSONDecodeError:
                continue
            normalized_candidate = candidate
            break
        if normalized_candidate is None:
            continue
        if not isinstance(parsed_line, dict):
            raise ValueError("JSONL lines must contain objects")
        rows.append((parsed_line, raw_candidate))
        pending_lines = []
    if pending_lines:
        raise ValueError("JSONL payload ended with an incomplete object")
    return rows


def _machine_payload_shape(payload: str) -> tuple[str, bool]:
    try:
        if payload.lstrip().startswith("["):
            parsed: Any = _strict_json_loads(payload)
        elif payload.lstrip().startswith("{"):
            try:
                parsed = _strict_json_loads(payload)
            except json.JSONDecodeError:
                parsed = _parse_jsonl(payload)
        else:
            parsed = _parse_jsonl(payload)
    except (json.JSONDecodeError, ValueError, TypeError):
        return "UNPARSEABLE", _has_machine_key_syntax(payload)
    keys = _nested_keys(parsed)
    shape = "ARRAY" if isinstance(parsed, list) else "OBJECT"
    return shape, bool(keys & _MACHINE_KEYS)


def _has_machine_key_syntax(payload: str) -> bool:
    for match in _MACHINE_KEY_TOKEN.finditer(payload):
        line_start = payload.rfind("\n", 0, match.start()) + 1
        prefix = payload[line_start : match.start()].lstrip()
        if not prefix or prefix.startswith(("{", "[", ",")):
            return True
    return False


def _strict_json_loads(payload: str) -> Any:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key: {key}")
            result[key] = value
        return result

    return json.loads(payload, object_pairs_hook=reject_duplicate_keys)


def _next_heading_fence(lines: list[str], start: int) -> int | None:
    """Allow short explanatory prose, but never cross another heading."""

    for index in range(start, min(len(lines), start + 13)):
        stripped = lines[index].strip()
        if _OPENING_FENCE.match(stripped):
            return index
        if index > start and stripped.startswith("#"):
            return None
    return None


def _is_closing_fence(line: str, opening_fence: str) -> bool:
    return _closing_fence_offset(line, opening_fence) is not None


def _closing_fence_offset(line: str, opening_fence: str) -> int | None:
    raw = line.rstrip("\r\n")
    stripped = raw.strip()
    if len(stripped) >= len(opening_fence) and set(stripped) == {
        opening_fence[0]
    }:
        return raw.find(opening_fence[0])
    if not raw.rstrip().endswith(opening_fence):
        return None
    offset = raw.rfind(opening_fence)
    prefix = raw[:offset]
    return offset if prefix.strip() else None


def _payload_field_signature(
    payload: str,
    declared_format: str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if declared_format not in {"json", "jsonl"}:
        return (), ()
    try:
        rows = _payload_rows(payload, declared_format)
    except (json.JSONDecodeError, ValueError, TypeError):
        return (), ()
    keys = tuple(sorted({str(key) for row in rows for key in row}))
    record_types = tuple(
        sorted(
            {
                str(row["record_type"])
                for row in rows
                if isinstance(row.get("record_type"), str)
            }
        )
    )
    return keys, record_types


def _nested_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        keys.update(str(key) for key in value)
        for nested in value.values():
            keys.update(_nested_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_nested_keys(nested))
    return keys


def _duplicate_names(
    occurrences: list[ArtifactOccurrence],
) -> tuple[list[str], list[str]]:
    by_name: dict[str, list[ArtifactOccurrence]] = {}
    for occurrence in occurrences:
        if occurrence.canonical_name is not None and not occurrence.overlapping_alias:
            by_name.setdefault(occurrence.canonical_name, []).append(occurrence)
    duplicates: list[str] = []
    conflicts: list[str] = []
    for name, rows in sorted(by_name.items()):
        if len(rows) <= 1:
            continue
        duplicates.append(name)
        hashes = {
            row.canonical_payload_sha256 or row.payload_sha256
            for row in rows
        }
        if len(hashes) > 1:
            conflicts.append(name)
    return duplicates, conflicts


def _mark_overlapping_aliases(
    occurrences: list[_RawOccurrence],
) -> list[_RawOccurrence]:
    """Retain nested wrappers in the census without double-counting one payload."""

    ordered = sorted(
        occurrences,
        key=lambda occurrence: (
            occurrence.char_start,
            -occurrence.char_end,
            occurrence.wrapper_kind,
        ),
    )
    result: list[_RawOccurrence] = []
    for occurrence in ordered:
        alias = False
        for parent in result:
            if parent.overlapping_alias:
                continue
            if parent.canonical_name != occurrence.canonical_name:
                continue
            spans_overlap = (
                parent.char_start < occurrence.char_end
                and occurrence.char_start < parent.char_end
            )
            if not spans_overlap:
                continue
            parent_parsed = _parse_payload(
                parent.canonical_name,
                parent.payload,
                parent.declared_format,
            )
            occurrence_parsed = _parse_payload(
                occurrence.canonical_name,
                occurrence.payload,
                occurrence.declared_format,
            )
            parent_hash = parent_parsed.get("canonical_payload_sha256")
            occurrence_hash = occurrence_parsed.get("canonical_payload_sha256")
            if parent_hash is not None and parent_hash == occurrence_hash:
                alias = True
                break
        result.append(replace(occurrence, overlapping_alias=alias))
    return sorted(result, key=lambda occurrence: (occurrence.char_start, occurrence.char_end))


def _heading_artifact_name(heading: str) -> str | None:
    match = _HEADING_NAME.match(heading)
    if match is not None:
        return match.group(1).strip()
    normalized = re.sub(r"[^a-z0-9]+", " ", heading.lower()).strip()
    return {
        "machine appendix c brain delta jsonl": "brain_delta.jsonl",
        "brain delta jsonl": "brain_delta.jsonl",
        "brain delta": "brain_delta.jsonl",
    }.get(normalized)


def _canonical_artifact_name(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _declared_format(name: str, language: str | None) -> str | None:
    canonical = _canonical_artifact_name(name)
    normalized_language = (language or "").strip().lower()
    if canonical.endswith(".jsonl") or normalized_language == "jsonl":
        return "jsonl"
    if canonical.endswith(".json") or normalized_language == "json":
        return "json"
    if canonical.endswith(".md") or normalized_language in {"md", "markdown"}:
        return "markdown"
    return normalized_language or None


def _strip_optional_fence(payload: str) -> str:
    lines = payload.strip().splitlines()
    if len(lines) >= 2:
        opening = _OPENING_FENCE.match(lines[0].strip())
        if opening is not None:
            closing_offset = _closing_fence_offset(
                lines[-1], opening.group("fence")
            )
            if closing_offset is not None:
                body = lines[1:-1]
                closing_prefix = lines[-1][:closing_offset]
                if closing_prefix.strip():
                    body.append(closing_prefix)
                return "\n".join(body).strip()
    return payload.strip()


def _byte_offsets(text: str, positions: set[int], *, bom_bytes: int) -> dict[int, int]:
    offsets: dict[int, int] = {}
    previous_character = 0
    previous_byte = bom_bytes
    for position in sorted(positions):
        previous_byte += len(text[previous_character:position].encode("utf-8"))
        offsets[position] = previous_byte
        previous_character = position
    return offsets
