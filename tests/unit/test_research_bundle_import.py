from __future__ import annotations

import json
from datetime import date, datetime, time

import pytest

from news_scalping_lab.contracts.models import BlindAnalysis, BlindPrediction, ResearchEpisode
from news_scalping_lab.research_import.bundle import BundleImportError, parse_bundle
from news_scalping_lab.research_import.importer import ResearchImporter
from news_scalping_lab.utils import KST, canonical_json, sha256_text


def _at(day: date, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=KST)


def _episode() -> ResearchEpisode:
    trade_day = date(2030, 1, 10)
    return ResearchEpisode(
        episode_id="EP-bundle-20300110",
        trade_date=trade_day,
        cutoff_at=_at(trade_day, time(8, 59, 59)),
        created_at=_at(trade_day, time(9, 0, 0)),
        research_version="bundle-test-v1",
        input_news_files=["news_20300110.csv"],
        input_news_hashes=["f" * 64],
        blind_artifact_sha256="bundle-test-hash",
        blind_seal_receipt={
            "schema_version": "nslab.blind_seal_receipt.v1",
            "phase": "BLIND_SEALED",
            "blind_artifact_sha256": "bundle-test-hash",
        },
        price_source_snapshot={"source": "mock", "allowed_through": "2030-01-09"},
        blind_analysis=BlindAnalysis(
            summary="Open-world blind analysis before D-day prices.",
            open_world_mechanisms=["current event -> possible beneficiary path"],
        ),
        available_from=_at(date(2030, 1, 11), time(0, 0, 0)),
    )


def _bundle_text(
    episode: ResearchEpisode,
    *,
    row_rows: list[dict[str, object]] | None = None,
    brain_rows: list[dict[str, object]] | None = None,
    source_rows: list[dict[str, object]] | None = None,
    tamper_blind_hash: bool = False,
    tamper_row_hash: bool = False,
    tamper_research_hash: bool = False,
    tamper_brain_hash: bool = False,
    tamper_seal_hash: bool = False,
    tamper_phase_hash: bool = False,
    phase_state_payload: dict[str, object] | None = None,
    row_disposition_coverage_ratio: float = 1.0,
    source_ledger_entry_count: int | None = None,
) -> str:
    blind = BlindPrediction(
        prediction_id="BP-bundle-20300110",
        trade_date=episode.trade_date,
        cutoff_at=episode.cutoff_at,
        created_at=episode.created_at,
        sealed_at=episode.created_at,
        blind_analysis=episode.blind_analysis,
    ).model_dump(mode="json")
    blind_hash = sha256_text(canonical_json(blind))
    blind["blind_artifact_sha256"] = "0" * 64 if tamper_blind_hash else blind_hash

    row_jsonl = "\n".join(
        json.dumps(row, ensure_ascii=False)
        for row in (
            row_rows
            or [
                {
                    "row_number": 1,
                    "event_id": "EVT-1",
                    "source_id": "SRC-1",
                    "provenance_source_ids": ["SRC-1"],
                    "disposition": "INCLUDED_BEFORE_CUTOFF",
                }
            ]
        )
    )
    brain_jsonl = "\n".join(
        json.dumps(row, ensure_ascii=False)
        for row in (brain_rows or [{"record_type": "memory_claim", "claim_id": "CLM-1"}])
    )
    source_payload_rows = (
        source_rows
        or [
            {
                "source_id": "SRC-1",
                "source_type": "news_csv_row",
                "title": "source title",
                "publisher": None,
                "url": "file://news.csv#row=1",
                "published_at": "2030-01-10T08:00:00+09:00",
                "retrieved_at": "2030-01-10T08:00:01+09:00",
                "time_verified": True,
                "available_before_cutoff": True,
                "usage_phase": "BLIND",
                "input_row_ids": [1],
                "event_ids": ["EVT-1"],
                "content_sha256": "abc",
                "notes": "test source",
            }
        ]
    )
    source_jsonl = "\n".join(
        json.dumps(row, ensure_ascii=False)
        for row in source_payload_rows
    )
    episode_payload = json.loads(episode.model_dump_json())
    receipt_sha = sha256_text(_write_json_text(episode_payload["blind_seal_receipt"]))
    phase_state = phase_state_payload or {
        "schema_version": "nslab.phase_state.v1",
        "run_id": "RUN-bundle-test",
        "phase": "BLIND_SEALED",
        "completed_phases": ["PHASE_A_NEWS_ONLY_BLIND"],
        "trade_date": episode.trade_date.isoformat(),
        "cutoff_at": episode.cutoff_at.isoformat(),
        "sealed_at": episode.created_at.isoformat(),
        "blind_seal_receipt_sha256": receipt_sha,
    }
    phase_state_json = _write_json_text(phase_state)
    manifest = {
        "schema_version": "nslab.bundle_manifest.v1",
        "run_id": "RUN-bundle-test",
        "trade_date": episode.trade_date.isoformat(),
        "blind_artifact_sha256": blind_hash,
        "row_disposition_sha256": "0" * 64 if tamper_row_hash else sha256_text(row_jsonl),
        "row_disposition_coverage_ratio": row_disposition_coverage_ratio,
        "source_ledger_sha256": sha256_text(source_jsonl),
        "source_ledger_entry_count": (
            len(source_payload_rows)
            if source_ledger_entry_count is None
            else source_ledger_entry_count
        ),
        "research_episode_sha256": (
            "0" * 64 if tamper_research_hash else sha256_text(canonical_json(episode_payload))
        ),
        "brain_delta_sha256": "0" * 64 if tamper_brain_hash else sha256_text(brain_jsonl),
        "blind_seal_receipt_sha256": (
            "0" * 64
            if tamper_seal_hash
            else receipt_sha
        ),
        "phase_state_sha256": "0" * 64 if tamper_phase_hash else sha256_text(phase_state_json),
    }
    return f"""---
schema_version: nslab.research_bundle.v1
artifact_type: research_episode_bundle
episode_id: {episode.episode_id}
trade_date: {episode.trade_date.isoformat()}
blind_artifact_sha256: {blind_hash}
---

<!-- NSLAB:BEGIN research_report.md -->
# Research Report

Blind report body.
<!-- NSLAB:END research_report.md -->

<!-- NSLAB:BEGIN blind_prediction.json -->
```json
{json.dumps(blind, ensure_ascii=False, indent=2, sort_keys=True)}
```
<!-- NSLAB:END blind_prediction.json -->

<!-- NSLAB:BEGIN research_episode.json -->
```json
{episode.model_dump_json(indent=2)}
```
<!-- NSLAB:END research_episode.json -->

<!-- NSLAB:BEGIN row_disposition.jsonl -->
```jsonl
{row_jsonl}
```
<!-- NSLAB:END row_disposition.jsonl -->

<!-- NSLAB:BEGIN brain_delta.jsonl -->
```jsonl
{brain_jsonl}
```
<!-- NSLAB:END brain_delta.jsonl -->

<!-- NSLAB:BEGIN source_ledger.jsonl -->
```jsonl
{source_jsonl}
```
<!-- NSLAB:END source_ledger.jsonl -->

<!-- NSLAB:BEGIN phase_state.json -->
```json
{phase_state_json}
```
<!-- NSLAB:END phase_state.json -->

<!-- NSLAB:BEGIN bundle_manifest.json -->
```json
{json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)}
```
<!-- NSLAB:END bundle_manifest.json -->
"""


def test_bundle_import_preserves_raw_and_saves_episode(tmp_path) -> None:
    source = tmp_path / "2030-01-10_nslab_episode_bundle_ffffffff.md"
    episode = _episode()
    source.write_text(_bundle_text(episode), encoding="utf-8")

    parsed = parse_bundle(source)
    imported = ResearchImporter(tmp_path).import_path(source, mode="bundle")

    assert parsed.validation["blind_hash_verified"]
    assert parsed.validation["row_disposition_hash_verified"]
    assert parsed.validation["row_disposition_coverage_verified"]
    assert parsed.validation["source_ledger_hash_verified"]
    assert parsed.validation["source_ledger_entry_count_verified"]
    assert parsed.validation["research_episode_hash_verified"]
    assert parsed.validation["brain_delta_hash_verified"]
    assert parsed.validation["blind_seal_receipt_hash_verified"]
    assert parsed.validation["phase_state_hash_verified"]
    assert parsed.validation["phase_state_receipt_link_verified"]
    assert parsed.validation["id_reference_integrity_verified"]
    assert parsed.jsonl_blocks["row_disposition.jsonl"][0]["row_number"] == 1
    assert parsed.jsonl_blocks["brain_delta.jsonl"][0]["record_type"] == "memory_claim"
    assert imported.episode_id == episode.episode_id
    assert any(item.source_type == "nslab_markdown_bundle" for item in imported.provenance)
    assert (tmp_path / "research" / "episodes" / f"{episode.episode_id}.json").exists()
    assert len(list((tmp_path / "data" / "raw" / "research").glob("*.md"))) == 1


def test_bundle_jsonl_contract_requires_record_type(tmp_path) -> None:
    source = tmp_path / "broken_bundle.md"
    source.write_text(_bundle_text(_episode(), brain_rows=[{"claim_id": "CLM-1"}]), encoding="utf-8")

    with pytest.raises(BundleImportError, match="record_type"):
        parse_bundle(source)


def test_bundle_import_rejects_mismatched_blind_hash(tmp_path) -> None:
    source = tmp_path / "tampered_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_blind_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["blind_hash_verified"]
    with pytest.raises(BundleImportError, match="blind_prediction.json hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_row_disposition_hash(tmp_path) -> None:
    source = tmp_path / "tampered_row_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_row_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["row_disposition_hash_verified"]
    with pytest.raises(BundleImportError, match="row_disposition.jsonl hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_incomplete_row_disposition_coverage(tmp_path) -> None:
    source = tmp_path / "incomplete_row_coverage_bundle.md"
    source.write_text(
        _bundle_text(_episode(), row_disposition_coverage_ratio=0.5),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert not parsed.validation["row_disposition_coverage_verified"]
    with pytest.raises(BundleImportError, match="row_disposition coverage ratio"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_research_episode_hash(tmp_path) -> None:
    source = tmp_path / "tampered_research_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_research_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["research_episode_hash_verified"]
    with pytest.raises(BundleImportError, match="research_episode.json hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_brain_delta_hash(tmp_path) -> None:
    source = tmp_path / "tampered_brain_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_brain_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["brain_delta_hash_verified"]
    with pytest.raises(BundleImportError, match="brain_delta.jsonl hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_blind_seal_receipt_hash(tmp_path) -> None:
    source = tmp_path / "tampered_seal_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_seal_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["blind_seal_receipt_hash_verified"]
    with pytest.raises(BundleImportError, match="blind_seal_receipt hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_phase_state_hash(tmp_path) -> None:
    source = tmp_path / "tampered_phase_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_phase_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["phase_state_hash_verified"]
    with pytest.raises(BundleImportError, match="phase_state.json hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_unlinked_phase_state(tmp_path) -> None:
    source = tmp_path / "unlinked_phase_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            phase_state_payload={
                "schema_version": "nslab.phase_state.v1",
                "run_id": "RUN-bundle-test",
                "phase": "BLIND_SEALED",
                "completed_phases": ["PHASE_A_NEWS_ONLY_BLIND"],
                "trade_date": "2030-01-10",
                "cutoff_at": "2030-01-10T08:59:59+09:00",
                "sealed_at": "2030-01-10T09:00:00+09:00",
                "blind_seal_receipt_sha256": "bad",
            },
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert not parsed.validation["phase_state_receipt_link_verified"]
    with pytest.raises(BundleImportError, match="phase_state.json is not linked"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_source_ledger_entry_count(tmp_path) -> None:
    source = tmp_path / "tampered_source_count_bundle.md"
    source.write_text(
        _bundle_text(_episode(), source_ledger_entry_count=99),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert not parsed.validation["source_ledger_entry_count_verified"]
    with pytest.raises(BundleImportError, match="source_ledger.jsonl entry count"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_invalid_id_references(tmp_path) -> None:
    source = tmp_path / "bad_references_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            source_rows=[
                {
                    "source_id": "SRC-1",
                    "source_type": "news_csv_row",
                    "title": "source title",
                    "publisher": None,
                    "url": "file://news.csv#row=99",
                    "published_at": "2030-01-10T08:00:00+09:00",
                    "retrieved_at": "2030-01-10T08:00:01+09:00",
                    "time_verified": True,
                    "available_before_cutoff": True,
                    "usage_phase": "BLIND",
                    "input_row_ids": [99],
                    "event_ids": ["EVT-1"],
                    "content_sha256": "abc",
                    "notes": "bad row reference",
                }
            ],
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert not parsed.validation["id_reference_integrity_verified"]
    with pytest.raises(BundleImportError, match="ID reference integrity"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_source_ledger_rejects_missing_required_fields(tmp_path) -> None:
    source = tmp_path / "bad_source_ledger_bundle.md"
    source.write_text(
        _bundle_text(_episode(), source_rows=[{"source_id": "SRC-1"}]),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="source_ledger.jsonl:1 missing fields"):
        parse_bundle(source)


def test_bundle_source_ledger_rejects_raw_body_duplication(tmp_path) -> None:
    source = tmp_path / "duplicated_source_body_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            source_rows=[
                {
                    "source_id": "SRC-1",
                    "source_type": "news_csv_row",
                    "title": "source title",
                    "publisher": None,
                    "url": "file://news.csv#row=1",
                    "published_at": "2030-01-10T08:00:00+09:00",
                    "retrieved_at": "2030-01-10T08:00:01+09:00",
                    "time_verified": True,
                    "available_before_cutoff": True,
                    "usage_phase": "BLIND",
                    "input_row_ids": [1],
                    "content_sha256": "abc",
                    "notes": "test source",
                    "body": "raw body must stay in the input artifact",
                }
            ],
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="must not duplicate body/content"):
        parse_bundle(source)


def test_bundle_row_disposition_rejects_raw_title_body(tmp_path) -> None:
    source = tmp_path / "bad_row_bundle.md"
    source.write_text(
        _bundle_text(_episode(), row_rows=[{"row_number": 1, "title": "raw"}]),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="must not duplicate title/body"):
        parse_bundle(source)


def test_auto_mode_rejects_invalid_bundle_instead_of_semantic_fallback(tmp_path) -> None:
    source = tmp_path / "2030-01-10_broken_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_blind_hash=True), encoding="utf-8")

    with pytest.raises(BundleImportError):
        ResearchImporter(tmp_path).import_path(source, mode="auto")

    assert not list((tmp_path / "research" / "episodes").glob("*.json"))


def _write_json_text(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
