from pathlib import Path

import pytest

from news_scalping_lab.research_import.repair_census import artifact_rows, census_source
from news_scalping_lab.utils import sha256_bytes


def test_census_retains_equivalent_duplicate_occurrences(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- NSLAB:BEGIN brain_delta.jsonl -->
{"record_id":"BD-1","record_type":"memory_claim"}
<!-- NSLAB:END brain_delta.jsonl -->

## brain_delta.jsonl
```jsonl
{ "record_id": "BD-1", "record_type": "memory_claim" }
```
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert len(census.artifact_occurrences) == 2
    assert census.duplicate_names == ["brain_delta.jsonl"]
    assert census.conflicting_duplicate_names == []
    assert census.explicit_record_count == 2


def test_census_reports_conflicting_duplicate_occurrences(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- BEGIN_ARTIFACT brain_delta.jsonl -->
{"record_id":"BD-1"}
<!-- END_ARTIFACT brain_delta.jsonl -->

## brain_delta.jsonl
```jsonl
{"record_id":"BD-2"}
```
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.conflicting_duplicate_names == ["brain_delta.jsonl"]


def test_census_normalizes_literal_newline_inside_jsonl_string(tmp_path: Path) -> None:
    bundle = tmp_path / "multiline-jsonl.md"
    bundle.write_text(
        """<!-- NSLAB:BEGIN source_ledger.jsonl -->
{"source_id":"SRC-1","source_type":"NEWS_CSV_ROW","body":"first
second"}
<!-- NSLAB:END source_ledger.jsonl -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)
    occurrence = next(
        item
        for item in census.artifact_occurrences
        if item.canonical_name == "source_ledger.jsonl"
    )
    rows = [
        item.row
        for item in artifact_rows(bundle)
        if item.canonical_name == "source_ledger.jsonl"
    ]

    assert occurrence.parse_status == "PARSED_WITH_NORMALIZED_NEWLINES"
    assert occurrence.row_count == 1
    assert len(rows) == 1
    assert rows[0]["body"] == "first\nsecond"


def test_census_accepts_json_named_jsonl_block(tmp_path: Path) -> None:
    bundle = tmp_path / "json-named-jsonl.md"
    bundle.write_text(
        "<!-- NSLAB:BEGIN id_registry.json -->\n"
        '{"id":"A","object_type":"brain_delta_record"}\n'
        '{"id":"B","object_type":"brain_delta_record"}\n'
        "<!-- NSLAB:END id_registry.json -->\n",
        encoding="utf-8",
    )

    census = census_source(bundle)

    occurrence = next(
        item
        for item in census.artifact_occurrences
        if item.canonical_name == "id_registry.json"
    )
    assert occurrence.parse_status == "PARSED"
    assert occurrence.row_count == 2


def test_census_accepts_scalar_json_metadata_array(tmp_path: Path) -> None:
    bundle = tmp_path / "metadata-array.md"
    bundle.write_text(
        "<!-- NSLAB:BEGIN required_blocks.json -->\n"
        '["research_report.md", "brain_delta.jsonl"]\n'
        "<!-- NSLAB:END required_blocks.json -->\n",
        encoding="utf-8",
    )

    census = census_source(bundle)

    occurrence = census.artifact_occurrences[0]
    assert occurrence.parse_status == "PARSED_METADATA"
    assert occurrence.top_level_shape == "ARRAY_SCALARS"
    assert occurrence.row_count is None
    assert occurrence.error is None
    assert census.unclaimed_machine_payloads == []
    assert artifact_rows(bundle) == []


def test_census_infers_json_for_extensionless_machine_marker(tmp_path: Path) -> None:
    bundle = tmp_path / "extensionless-json.md"
    bundle.write_text(
        "<!-- NSLAB:BEGIN current_run_attestation -->\n"
        '{"schema_version":"nslab.current_run_attestation.v1","run_id":"RUN-1"}\n'
        "<!-- NSLAB:END current_run_attestation -->\n",
        encoding="utf-8",
    )

    census = census_source(bundle)

    occurrence = census.artifact_occurrences[0]
    assert occurrence.parse_status == "PARSED"
    assert occurrence.row_count == 1
    assert census.unclaimed_machine_payloads == []


def test_census_detects_unclaimed_machine_fence(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """# Report

```json
{"record_id":"BD-1","record_type":"memory_claim"}
```
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert len(census.unclaimed_machine_payloads) == 1
    assert census.unclaimed_machine_payloads[0].detected_shape == "OBJECT"


def test_census_excludes_valid_json_front_matter_from_unclaimed_payloads(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "json-front-matter.md"
    bundle.write_text(
        "---\n"
        '{"artifact_type":"research_episode_bundle","episode_id":"EP-1"}\n'
        "---\n"
        "<!-- NSLAB:BEGIN brain_delta.jsonl -->\n"
        '{"record_id":"BD-1","record_type":"memory_claim"}\n'
        "<!-- NSLAB:END brain_delta.jsonl -->\n",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.explicit_record_count == 1
    assert census.unclaimed_machine_payloads == []


def test_census_rejects_invalid_utf8_without_replacement(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_bytes(b"valid\n\xff")

    census = census_source(bundle)

    assert census.strict_utf8_ok is False
    assert census.decode_error
    assert census.artifact_occurrences == []


def test_census_byte_offsets_include_bom_and_unicode(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """한글
## brain_delta.jsonl
```jsonl
{"record_id":"BD-1"}
```
""",
        encoding="utf-8-sig",
    )

    census = census_source(bundle)

    occurrence = census.artifact_occurrences[0]
    raw = bundle.read_bytes()
    assert raw[occurrence.byte_start : occurrence.byte_end].decode("utf-8").startswith(
        "## brain_delta.jsonl"
    )


def test_structure_fingerprint_ignores_payload_values(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    template = """## brain_delta.jsonl
```jsonl
{{"record_id":"{record_id}","record_type":"memory_claim"}}
```
"""
    first.write_text(template.format(record_id="BD-1"), encoding="utf-8")
    second.write_text(template.format(record_id="BD-2"), encoding="utf-8")

    assert census_source(first).structure_fingerprint == census_source(
        second
    ).structure_fingerprint


def test_artifact_rows_have_stable_origin_keys(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """## brain_delta.jsonl
```jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
{"record_id":"BD-2","record_type":"memory_claim"}
```
""",
        encoding="utf-8",
    )

    first = artifact_rows(bundle)
    second = artifact_rows(bundle)

    assert [row.origin_key for row in first] == [row.origin_key for row in second]
    assert [row.row["record_id"] for row in first] == ["BD-1", "BD-2"]


def test_census_accepts_nslab_artifact_markers(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- NSLAB_ARTIFACT_BEGIN brain_delta.jsonl -->
{"record_id":"BD-1","record_type":"memory_claim"}
<!-- NSLAB_ARTIFACT_END brain_delta.jsonl -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.explicit_record_count == 1
    assert census.artifact_occurrences[0].wrapper_kind == "NSLAB_ARTIFACT"


def test_census_claims_input_coverage_receipt_marker(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- NSLAB:INPUT_COVERAGE_RECEIPT_BEGIN -->
```json
{"schema_version":"nslab.input_coverage_receipt.v1","csv_row_count":1}
```
<!-- NSLAB:INPUT_COVERAGE_RECEIPT_END -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.unclaimed_machine_payloads == []
    assert census.artifact_occurrences[0].canonical_name == "input_coverage_receipt.json"
    assert census.artifact_occurrences[0].parse_status == "PARSED"
    assert artifact_rows(bundle)[0].row["schema_version"] == "nslab.input_coverage_receipt.v1"


def test_census_accepts_inline_closing_fence(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- NSLAB:BEGIN acquisition_receipt.json -->
```json
{"schema_version":"nslab.acquisition_receipt.v1"}```
<!-- NSLAB:END acquisition_receipt.json -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.unclaimed_machine_payloads == []
    assert census.artifact_occurrences[0].parse_status == "PARSED"


def test_census_treats_declared_code_artifact_as_opaque_text(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- NSLAB:BEGIN rank_and_seal_blind_repaired.py -->
print({"record_id": "not-a-ledger-row"})
<!-- NSLAB:END rank_and_seal_blind_repaired.py -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.unclaimed_machine_payloads == []
    occurrence = census.artifact_occurrences[0]
    assert occurrence.parse_status == "OPAQUE_TEXT"
    assert occurrence.top_level_shape == "TEXT"
    assert occurrence.row_count is None


def test_census_accepts_heading_prose_and_tilde_fence(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """## brain_delta.jsonl
The following payload is the canonical record appendix.

~~~~jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
~~~~
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.explicit_record_count == 1
    assert census.unclaimed_machine_payloads == []


def test_census_does_not_promote_json_phase_value_to_heading_artifact(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "phase-report.md"
    bundle.write_text(
        "```json\n"
        '{"ordered_phases":["BLIND_SEAL","BRAIN_DELTA","RENDER_VALIDATE"]}\n'
        "```\n",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.artifact_occurrences == []
    assert census.unclaimed_machine_payloads == []


def test_census_rejects_duplicate_json_object_keys(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """## brain_delta.jsonl
```jsonl
{"record_id":"BD-1","record_id":"BD-2","record_type":"memory_claim"}
```
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.artifact_occurrences[0].parse_status == "PARSE_ERROR"
    assert "duplicate JSON object key" in str(census.artifact_occurrences[0].error)


def test_artifact_rows_bind_canonical_and_exact_row_hashes(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text(
        """## brain_delta.jsonl
```jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
```
""",
        encoding="utf-8",
    )
    second.write_text(
        """## brain_delta.jsonl
```jsonl
{ "record_id": "BD-1", "record_type": "memory_claim" }
```
""",
        encoding="utf-8",
    )

    first_row = artifact_rows(first)[0]
    second_row = artifact_rows(second)[0]

    assert first_row.canonical_row_sha256 == second_row.canonical_row_sha256
    assert first_row.raw_row_bytes_sha256 != second_row.raw_row_bytes_sha256


@pytest.mark.parametrize(
    "payload, expected_reason",
    [
        (
            "````jsonl\n"
            '{"record_id":"BD-LOST","record_type":"memory_claim"}\n',
            "unclosed machine-looking fence",
        ),
        (
            "~~~~jsonl\n"
            '{"record_id":"BD-1","record_type":"memory_claim"}\n'
            '{"record_id":"BD-2","record_type":\n'
            "~~~~~\n",
            "unparseable machine-looking fenced payload",
        ),
    ],
)
def test_census_detects_unclosed_long_and_tilde_machine_fences_with_exact_hash(
    tmp_path: Path,
    payload: str,
    expected_reason: str,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(f"# Report\n\n{payload}", encoding="utf-8")

    census = census_source(bundle)

    assert len(census.unclaimed_machine_payloads) == 1
    occurrence = census.unclaimed_machine_payloads[0]
    raw = bundle.read_bytes()
    assert expected_reason in occurrence.reason
    assert occurrence.payload_sha256 == sha256_bytes(
        raw[occurrence.byte_start : occurrence.byte_end]
    )


def test_census_detects_unknown_artifact_wrapper_as_one_exact_span(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """# Report

<!-- CUSTOM_PAYLOAD_BEGIN lost.jsonl -->
~~~~jsonl
{"record_id":"BD-LOST","record_type":"memory_claim"}
~~~~
<!-- CUSTOM_PAYLOAD_END lost.jsonl -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert len(census.unclaimed_machine_payloads) == 1
    occurrence = census.unclaimed_machine_payloads[0]
    raw = bundle.read_bytes()
    raw_span = raw[occurrence.byte_start : occurrence.byte_end]
    assert occurrence.detected_shape == "UNKNOWN_ARTIFACT_WRAPPER"
    assert raw_span.startswith(b"<!-- CUSTOM_PAYLOAD_BEGIN lost.jsonl -->")
    assert raw_span.endswith(b"<!-- CUSTOM_PAYLOAD_END lost.jsonl -->")
    assert occurrence.payload_sha256 == sha256_bytes(raw_span)


def test_census_detects_bare_machine_jsonl_but_ignores_markdown_prose(
    tmp_path: Path,
) -> None:
    machine = tmp_path / "machine.md"
    machine.write_text(
        """# Appendix

{"record_id":"BD-1","record_type":"memory_claim"}
{"record_id":"BD-2","record_type":"memory_claim"}

This prose remains outside the payload.
""",
        encoding="utf-8",
    )
    prose = tmp_path / "prose.md"
    prose.write_text(
        """# Report

This paragraph discusses record types and JSON formatting in ordinary prose.

```python
record_type = build_record_type()
```

```markdown
The literal `"record_type":` is documentation, not a machine payload.
```
""",
        encoding="utf-8",
    )

    machine_census = census_source(machine)
    prose_census = census_source(prose)

    assert len(machine_census.unclaimed_machine_payloads) == 1
    assert machine_census.unclaimed_machine_payloads[0].detected_shape == "ARRAY"
    assert prose_census.unclaimed_machine_payloads == []


def test_artifact_rows_skip_claimed_parse_error_for_quality_reporting(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """## brain_delta.jsonl
```jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
{"record_id":"BD-2","record_type":
```
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.artifact_occurrences[0].parse_status == "PARSE_ERROR"
    assert artifact_rows(bundle) == []


def test_heading_claim_does_not_hide_machine_payload_before_its_fence(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """## brain_delta.jsonl
{"record_id":"BD-LOST","record_type":"memory_claim"}

The canonical payload follows.
```jsonl
{"record_id":"BD-CLAIMED","record_type":"memory_claim"}
```
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.explicit_record_count == 1
    assert len(census.unclaimed_machine_payloads) == 1
    occurrence = census.unclaimed_machine_payloads[0]
    assert bundle.read_bytes()[occurrence.byte_start : occurrence.byte_end].startswith(
        b'{"record_id":"BD-LOST"'
    )


def test_marker_wrapper_claims_long_tilde_fence_without_false_unclaimed(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "bundle.md"
    bundle.write_text(
        """<!-- NSLAB:BEGIN brain_delta.jsonl -->
~~~~jsonl
{"record_id":"BD-1","record_type":"memory_claim"}
~~~~~
<!-- NSLAB:END brain_delta.jsonl -->
""",
        encoding="utf-8",
    )

    census = census_source(bundle)

    assert census.explicit_record_count == 1
    assert census.artifact_occurrences[0].parse_status == "PARSED"
    assert census.unclaimed_machine_payloads == []
