"""Clean CSV files so they remain valid UTF-8 text for content sniffers."""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path

ALLOWED_CONTROL_CHARS = {"\t", "\n", "\r"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Remove non-text C0 control characters from UTF-8 CSV files while "
            "preserving all other content."
        )
    )
    parser.add_argument("csv_path", type=Path)
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="Rewrite the file after validating the cleaned CSV parses.",
    )
    args = parser.parse_args()

    report = clean_text_csv(args.csv_path, in_place=args.in_place)
    print(
        "\n".join(
            [
                f"path={report.path}",
                f"input_bytes={report.input_bytes}",
                f"output_bytes={report.output_bytes}",
                f"row_count={report.row_count}",
                f"removed_control_chars={report.removed_control_chars}",
                f"removed_by_codepoint={dict(report.removed_by_codepoint)}",
                f"written={report.written}",
            ]
        )
    )


class CleanCsvReport:
    def __init__(
        self,
        *,
        path: Path,
        input_bytes: int,
        output_bytes: int,
        row_count: int,
        removed_by_codepoint: Counter[str],
        written: bool,
    ) -> None:
        self.path = path
        self.input_bytes = input_bytes
        self.output_bytes = output_bytes
        self.row_count = row_count
        self.removed_by_codepoint = removed_by_codepoint
        self.removed_control_chars = sum(removed_by_codepoint.values())
        self.written = written


def clean_text_csv(path: Path, *, in_place: bool = False) -> CleanCsvReport:
    original_bytes = path.read_bytes()
    original_text = original_bytes.decode("utf-8")
    cleaned_text, removed = remove_non_text_control_chars(original_text)
    row_count = _csv_row_count(cleaned_text)
    cleaned_bytes = cleaned_text.encode("utf-8")
    if in_place and cleaned_bytes != original_bytes:
        path.write_bytes(cleaned_bytes)
    return CleanCsvReport(
        path=path,
        input_bytes=len(original_bytes),
        output_bytes=len(cleaned_bytes),
        row_count=row_count,
        removed_by_codepoint=removed,
        written=in_place and cleaned_bytes != original_bytes,
    )


def remove_non_text_control_chars(text: str) -> tuple[str, Counter[str]]:
    removed: Counter[str] = Counter()
    cleaned_chars: list[str] = []
    for char in text:
        if ord(char) < 32 and char not in ALLOWED_CONTROL_CHARS:
            removed[f"U+{ord(char):04X}"] += 1
            continue
        cleaned_chars.append(char)
    return "".join(cleaned_chars), removed


def _csv_row_count(text: str) -> int:
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        raise ValueError("CSV is empty after cleaning")
    return len(rows) - 1


if __name__ == "__main__":
    main()
