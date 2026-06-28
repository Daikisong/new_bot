from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from news_scalping_lab.audits.coverage import audit_coverage
from news_scalping_lab.config import Settings, ensure_project_dirs
from news_scalping_lab.records.store import BrainRecordStore, audit_record_store
from news_scalping_lab.research_import import importer as importer_module
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.research_import.versioned_bundle import (
    BundleImportResult,
    bundle_schema_version,
    import_versioned_bundle,
    inspect_versioned_bundle,
    parse_generic_bundle,
)
from news_scalping_lab.training import export_training
from news_scalping_lab.utils import sha256_text
from news_scalping_lab.warehouse import WarehouseStore

PROJECT_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE2_BUNDLE = PROJECT_ROOT / "docs" / "example2.md"
RESEARCH_PROMPT = PROJECT_ROOT / "docs" / "research_prompt.md"
SESSION_PROMPT = PROJECT_ROOT / "docs" / "session_prompt.md"
DOCS_README = PROJECT_ROOT / "docs" / "README.md"


def _ensure_tmp_project(root: Path) -> None:
    ensure_project_dirs(Settings(project_root=root))


def _jsonl(path: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _import_example2(root: Path) -> BundleImportResult:
    _ensure_tmp_project(root)
    result = import_versioned_bundle(EXAMPLE2_BUNDLE, root=root, accepted=True)
    assert result.status == "imported"
    assert result.accepted is True
    return result


def _raw_brain_delta_rows() -> list[dict[str, object]]:
    return parse_generic_bundle(EXAMPLE2_BUNDLE).jsonl_blocks["brain_delta.jsonl"]


def _raw_record_id(row: dict[str, object]) -> str:
    for key in ("record_id", "brain_delta_id", "id"):
        value = row.get(key)
        if isinstance(value, str) and value:
            return value
    raise AssertionError(f"raw brain_delta row has no record id: {row}")


def _raw_training_eligible(row: dict[str, object]) -> bool:
    if row.get("training_eligible") is not True:
        return False
    if row.get("record_type") != "blind_leader_preference_pair":
        return True
    preferred = row.get("blind_preferred_ticker") or row.get(
        "blind_preferred_candidate_id"
    )
    rejected = row.get("blind_rejected_ticker") or row.get(
        "blind_rejected_candidate_id"
    )
    return isinstance(preferred, str) and bool(preferred) and isinstance(
        rejected, str
    ) and bool(rejected)


def test_research_import_auto_delegates_gold_bundle_to_versioned_import(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_tmp_project(tmp_path)

    def fail_legacy_bundle_import(path: Path) -> object:
        raise AssertionError(f"legacy bundle import was called for {path}")

    monkeypatch.setattr(
        importer_module,
        "import_bundle_episode",
        fail_legacy_bundle_import,
    )

    result = ResearchImporter(tmp_path).import_path(EXAMPLE2_BUNDLE, mode="auto")

    assert isinstance(result, BundleImportResult)
    assert result.status == "imported"
    assert result.accepted is False
    assert result.adapter_name == "v23-direct-ingest"
    assert result.record_count == len(_raw_brain_delta_rows())


def test_research_import_does_not_force_v11_bundle_into_legacy_episode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _ensure_tmp_project(tmp_path)

    def fail_legacy_bundle_import(path: Path) -> object:
        raise AssertionError(f"legacy ResearchEpisode import was called for {path}")

    monkeypatch.setattr(
        importer_module,
        "import_bundle_episode",
        fail_legacy_bundle_import,
    )

    result = ResearchImporter(tmp_path).import_path(EXAMPLE2_BUNDLE, mode="auto")

    assert isinstance(result, BundleImportResult)
    assert result.bundle_schema_version == "nslab.research_bundle.v11"
    assert result.adapter_name == "v23-direct-ingest"


def test_import_bundle_and_import_auto_have_same_record_count_for_example2(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    auto_root = tmp_path / "auto"
    _ensure_tmp_project(bundle_root)
    _ensure_tmp_project(auto_root)

    bundle_result = import_versioned_bundle(
        EXAMPLE2_BUNDLE,
        root=bundle_root,
        accepted=False,
    )
    auto_result = ResearchImporter(auto_root).import_path(EXAMPLE2_BUNDLE, mode="auto")

    assert isinstance(auto_result, BundleImportResult)
    assert auto_result.record_count == bundle_result.record_count
    assert (
        auto_result.training_eligible_record_count
        == bundle_result.training_eligible_record_count
    )
    assert auto_result.adapter_name == bundle_result.adapter_name


def test_example2_gold_bundle_import_loss_zero(tmp_path: Path) -> None:
    raw_rows = _raw_brain_delta_rows()
    parsed = parse_generic_bundle(EXAMPLE2_BUNDLE)
    manifest = parsed.json_blocks["bundle_manifest.json"]
    validation_report = parsed.json_blocks["validation_report.json"]
    inspection = inspect_versioned_bundle(EXAMPLE2_BUNDLE)
    result = _import_example2(tmp_path)

    assert bundle_schema_version(parsed) == "nslab.research_bundle.v11"
    assert manifest["bundle_status"] == "ACCEPT_FULL"
    assert manifest["validator_exit_code"] == 0
    assert validation_report["validator_exit_code"] == 0
    assert inspection["validation_passed"] is True
    assert inspection["direct_brain_ingest_ready"] is True
    assert inspection["raw_record_count"] == len(raw_rows)
    assert inspection["normalized_record_count"] == len(raw_rows)
    assert inspection["raw_normalized_record_count_matches"] is True
    assert inspection["training_eligible_count_matches_raw"] is True
    assert inspection["record_id_set_matches_raw"] is True
    assert inspection["record_type_counts_match_raw"] is True
    assert inspection["raw_payload_hashes_match"] is True
    assert inspection["missing_source_reference_count"] == 0
    assert inspection["missing_payload_reference_count"] == 0
    assert inspection["import_loss_audit_passed"] is True
    assert inspection["dropped_record_count"] == 0
    assert inspection["quarantined_record_count"] == 0
    assert result.record_count == len(raw_rows)


def test_example2_gold_bundle_record_manifest_matches_raw(tmp_path: Path) -> None:
    result = _import_example2(tmp_path)
    assert result.record_path is not None
    assert result.manifest_path is not None
    raw_rows = _raw_brain_delta_rows()
    raw_ids = sorted(_raw_record_id(row) for row in raw_rows)
    raw_type_counts = dict(
        sorted(Counter(str(row.get("record_type") or "unknown") for row in raw_rows).items())
    )
    raw_training_eligible_count = sum(1 for row in raw_rows if _raw_training_eligible(row))
    record_rows = _jsonl(result.record_path)
    manifest = _read_json(result.manifest_path)
    audit = audit_record_store(tmp_path, deep=True)

    assert sorted(row["record_id"] for row in record_rows) == raw_ids
    assert manifest["record_count"] == len(raw_rows)
    assert manifest["record_ids"] == raw_ids
    assert manifest["training_eligible_record_count"] == raw_training_eligible_count
    assert manifest["record_counts_by_type"] == raw_type_counts
    assert manifest["records_sha256"] == sha256_text(
        result.record_path.read_text(encoding="utf-8")
    )
    assert audit["passed"] is True
    assert audit["brain_delta_count_mismatch_episode_ids"] == []
    assert audit["brain_delta_record_id_mismatch_episode_ids"] == []
    assert audit["brain_delta_training_eligible_mismatch_episode_ids"] == []
    assert audit["brain_delta_type_count_mismatch_episode_ids"] == []


def test_example2_gold_bundle_warehouse_projection_matches_records(
    tmp_path: Path,
) -> None:
    _import_example2(tmp_path)
    records = BrainRecordStore(tmp_path).list_records()
    counts = WarehouseStore(tmp_path).rebuild_all()
    coverage = audit_coverage(tmp_path)

    assert counts["brain_records"] == len(records)
    assert counts["issuer_day_cases"] == sum(
        1 for record in records if record.record_type == "supervised_issuer_day_case"
    )
    assert counts["direct_event_cases"] == sum(
        1 for record in records if record.record_type == "supervised_direct_event_case"
    )
    assert counts["leader_pairs"] == sum(
        1 for record in records if record.record_type == "blind_leader_preference_pair"
    )
    assert coverage["warehouse_projection_synced"] is True
    assert coverage["warehouse_count_mismatches"] == {}


def test_example2_gold_bundle_training_export_uses_only_eligible_records(
    tmp_path: Path,
) -> None:
    _import_example2(tmp_path)
    records_by_id = {record.record_id: record for record in BrainRecordStore(tmp_path).list_records()}
    result = export_training(tmp_path, kind="sft")
    manifest = _read_json(result.manifest_path)
    rows = _jsonl(result.path)
    exported_ids = {
        record_id
        for row in rows
        if isinstance(record_id := row.get("record_id"), str) and record_id
    }

    assert manifest["source_record_count"] == len(records_by_id)
    assert set(manifest["exported_record_ids"]) == exported_ids
    assert exported_ids <= set(manifest["eligible_record_ids"])
    assert all(records_by_id[record_id].training_eligible for record_id in exported_ids)
    assert set(manifest["skipped_record_ids"]).isdisjoint(exported_ids)


def test_example2_gold_bundle_preference_export_uses_only_blind_leader_pairs(
    tmp_path: Path,
) -> None:
    _import_example2(tmp_path)
    records = BrainRecordStore(tmp_path).list_records()
    result = export_training(tmp_path, kind="preference")
    manifest = _read_json(result.manifest_path)
    rows = _jsonl(result.path)
    exported_ids = {
        record_id
        for row in rows
        if isinstance(record_id := row.get("record_id"), str) and record_id
    }
    expected_pair_ids = {
        record.record_id
        for record in records
        if record.training_eligible
        and record.record_type == "blind_leader_preference_pair"
    }

    assert exported_ids == expected_pair_ids
    assert set(manifest["eligible_record_ids"]) == expected_pair_ids
    assert all(row["record_type"] == "blind_leader_preference_pair" for row in rows)


def test_research_prompt_gold_contract_matches_supported_adapter_versions() -> None:
    prompt = RESEARCH_PROMPT.read_text(encoding="utf-8")
    inspection = inspect_versioned_bundle(EXAMPLE2_BUNDLE)

    for required in (
        "schema_version: nslab.research_bundle.v11",
        "execution_protocol_version: nslab.brain_grade_semantic_provenance_locked.v11",
        "direct_brain_ingest_ready",
        "direct_ingest_contract.json",
        "validation_report.json",
        "bundle_manifest.json",
        "brain_delta.jsonl",
        "record_import_manifest",
        "canonical_graph_sha256",
        "renderer_version",
        "validator_version",
    ):
        assert required in prompt
    assert inspection["adapter"] == "v23-direct-ingest"
    assert inspection["manifest_schema_version"] == "nslab.bundle_manifest.v23"
    assert inspection["validation"]["direct_ingest_contract_supported"] is True


def test_docs_readme_identifies_current_production_inputs() -> None:
    readme = DOCS_README.read_text(encoding="utf-8")

    assert "current production prompt = `docs/research_prompt.md`" in readme
    assert "current gold output shape = `docs/example2.md`" in readme
    assert "do not use `example.md` as gold" in readme
    assert "do not use archived prompts or archived examples for production" in readme


def test_session_prompt_points_to_main_research_prompt_raw_url() -> None:
    prompt = SESSION_PROMPT.read_text(encoding="utf-8")

    assert (
        "https://raw.githubusercontent.com/Daikisong/new_bot/refs/heads/main/"
        "docs/research_prompt.md"
    ) in prompt
    assert (
        "https://raw.githubusercontent.com/Daikisong/new_bot/"
        "b7c8d7b4ff20c2c83a0689a453333ca46a013d99/docs/research_prompt.md"
    ) not in prompt


def test_direct_ingest_contract_required_only_when_adapter_supports_it() -> None:
    inspection = inspect_versioned_bundle(EXAMPLE2_BUNDLE)

    assert inspection["adapter"] == "v23-direct-ingest"
    assert inspection["direct_ingest_contract_present"] is True
    assert inspection["validation"]["direct_ingest_contract_supported"] is True
    assert inspection["direct_ingest_schema_contract_verified"] is True
    assert inspection["direct_ingest_validator_exit_code"] == 0


def test_unsupported_direct_ingest_contract_shape_quarantined_without_drop(
    tmp_path: Path,
) -> None:
    parsed = parse_generic_bundle(EXAMPLE2_BUNDLE)
    tampered = EXAMPLE2_BUNDLE.read_text(encoding="utf-8").replace(
        '"direct_brain_ingest_ready": true',
        '"direct_brain_ingest_ready": false',
        1,
    )
    bundle = tmp_path / "unsupported_direct_ingest_contract.md"
    bundle.write_text(tampered, encoding="utf-8")
    _ensure_tmp_project(tmp_path)

    with pytest.raises(ValueError):
        import_versioned_bundle(bundle, root=tmp_path, accepted=True)

    report = _read_json(tmp_path / "diagnostics" / "bundle_import_report.json")
    quarantine = Path(str(report["quarantine"]))
    assert report["status"] == "BUNDLE_VALIDATION_FAILED"
    assert report["raw_record_count"] == len(parsed.jsonl_blocks["brain_delta.jsonl"])
    assert report["normalized_record_count"] == len(parsed.jsonl_blocks["brain_delta.jsonl"])
    assert report["dropped_record_count"] == 0
    assert report["quarantined_record_count"] == 1
    assert quarantine.exists()
