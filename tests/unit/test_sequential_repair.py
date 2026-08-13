import sys
from pathlib import Path

import pytest

from news_scalping_lab.research_import.versioned_bundle import VersionedBundleImportError
from news_scalping_lab.tools import sequential_repair


def test_isolated_validation_records_import_rejection(monkeypatch, tmp_path: Path) -> None:
    def reject(*args: object, **kwargs: object) -> object:
        raise VersionedBundleImportError("bundle validation failed: missing payload")

    monkeypatch.setattr(sequential_repair, "import_versioned_bundle", reject)
    monkeypatch.setattr(sequential_repair, "tree_snapshot", lambda: {"sha256": "same"})

    audit, ephemeral = sequential_repair._isolated_validation(
        tmp_path / "candidate.repaired.md",
        production_before={"sha256": "same"},
    )

    assert audit["passed"] is False
    assert audit["import_result"]["status"] == "validation_failed"
    assert audit["deep_audit"]["passed"] is False
    assert audit["isolated_root_removed"] is True
    assert ephemeral["passed"] is False


def test_source_date_accepts_hyphenated_deferred_filename() -> None:
    assert (
        sequential_repair._source_date(
            Path("2018-09-24_nslab_deferred_non_trading_ac1759ee.md")
        )
        == "20180924"
    )


def test_quarantine_existing_repaired_artifacts_moves_them_out_of_ingest_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repaired_root = tmp_path / "repaired"
    year_root = repaired_root / "2024"
    year_root.mkdir(parents=True)
    source_sha = "a" * 64
    stale = year_root / f"20241017_bundle.{source_sha[:12]}.old.repaired.md"
    stale.write_text("stale", encoding="utf-8")
    monkeypatch.setattr(sequential_repair, "REPAIRED_ROOT", repaired_root)

    moved = sequential_repair._quarantine_existing_repaired_artifacts(
        Path("20241017_bundle.md"),
        source_sha,
    )

    assert moved == [
        str(repaired_root / "quarantined" / "2024" / stale.name)
    ]
    assert not stale.exists()
    assert Path(moved[0]).read_text(encoding="utf-8") == "stale"


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("DEFERRED_NON_TRADING", True),
        ("PARTIAL_PRICE_SOURCE_MISSING", True),
        ("PRESERVED_SOURCE_PAYLOAD_ABSENT", True),
        ("PRESERVED_PARTIAL_NOT_CURRENT_GOLD", True),
        ("ADAPTER_REQUIRED", False),
    ],
)
def test_resume_only_skips_terminal_or_ready_statuses(
    status: str,
    expected: bool,
) -> None:
    assert sequential_repair._is_resumable(
        {
            "final_status": status,
            "ready_for_import": False,
        }
    ) is expected


def test_cli_rejects_multi_source_invocation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["sequential_repair", "--max-files", "2"],
    )

    with pytest.raises(SystemExit) as error:
        sequential_repair.main()

    assert error.value.code == 2


def test_process_source_records_repair_parse_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source = tmp_path / "episode.md"
    source.write_text(
        """---
schema_version: nslab.research_bundle.v11
episode_id: NSLAB-20300110-parse-error
trade_date: 2030-01-10
---
<!-- NSLAB:BEGIN brain_delta.jsonl -->
{"record_id":"BD-1","record_type":"memory_claim"}
<!-- NSLAB:END brain_delta.jsonl -->
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(sequential_repair, "WORK_ROOT", tmp_path / "work")

    def fail(*args: object, **kwargs: object) -> object:
        raise VersionedBundleImportError("malformed artifact")

    monkeypatch.setattr(sequential_repair, "repair_bundle", fail)

    result = sequential_repair.process_source(
        source,
        {"engine_digest": "e"},
    )

    assert result["final_status"] == "ADAPTER_REQUIRED"
    assert result["ready_for_import"] is False
    assert result["error_type"] == "VersionedBundleImportError"
