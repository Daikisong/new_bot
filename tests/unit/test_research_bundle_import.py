from __future__ import annotations

import json
from datetime import date, datetime, time

import pytest

from news_scalping_lab.context.final_synthesis import (
    FINAL_SYNTHESIS_REQUIRED_INPUTS,
    final_synthesis_input_summary,
)
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
        blind_integrity={
            "blind_context_mode": "NEWS_ONLY_STRICT",
            "blind_web_search_call_count": 0,
            "blind_price_repository_access_count": 0,
            "blind_current_price_access_count": 0,
            "no_d_outcome_exposed": True,
        },
        blind_seal_receipt={
            "schema_version": "nslab.blind_seal_receipt.v1",
            "phase": "BLIND_SEALED",
            "blind_artifact_sha256": "bundle-test-hash",
            "no_d_outcome_exposed": True,
        },
        price_source_snapshot={"source": "mock", "allowed_through": "2030-01-09"},
        blind_analysis=BlindAnalysis(
            summary="Open-world blind analysis before D-day prices.",
            open_world_mechanisms=["current event -> possible beneficiary path"],
        ),
        available_from=_at(date(2030, 1, 11), time(0, 0, 0)),
    )


def _final_synthesis_payload() -> dict[str, object]:
    return {
        "required_inputs": list(FINAL_SYNTHESIS_REQUIRED_INPUTS),
        "current_news": ["bundle news"],
        "open_world_first_analysis": [],
        "news_novelty_review": {"findings": []},
        "additional_semantic_retrieval": {"rows": [], "episodes": []},
        "open_world_candidate_expansion": {"findings": []},
        "web_research": {"sources": []},
        "global_brain": [],
        "all_shard_brains": [],
        "all_shard_contributions": [],
        "retrieved_raw_episodes": [],
        "positive_cases": [],
        "negative_cases": [],
        "counterexamples": [],
        "candidate_research": {"candidates": []},
        "candidate_web_checks": [],
        "candidate_verification": {"findings": []},
        "red_team_output": {"candidate_findings": []},
        "d_minus_one_market_data": {"snapshots": []},
        "company_memory": [],
        "market_memory": [],
    }


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
    tamper_receipt_contract: bool = False,
    tamper_phase_hash: bool = False,
    tamper_phase_contract: bool = False,
    include_candidate_verification: bool = False,
    tamper_candidate_verification_contract: bool = False,
    include_final_synthesis_context: bool = False,
    tamper_final_synthesis_context_contract: bool = False,
    phase_state_payload: dict[str, object] | None = None,
    row_disposition_coverage_ratio: float = 1.0,
    blind_context_mode: str = "NEWS_ONLY_STRICT",
    blind_web_search_call_count: int = 0,
    blind_price_repository_access_count: int = 0,
    blind_current_price_access_count: int = 0,
    no_d_outcome_exposed: bool = True,
    source_ledger_entry_count: int | None = None,
    manifest_validation_overrides: dict[str, bool] | None = None,
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
    source_payload_rows = [
        {**row, "source_url": row["url"]}
        if "url" in row and "source_url" not in row
        else row
        for row in source_payload_rows
    ]
    source_jsonl = "\n".join(
        json.dumps(row, ensure_ascii=False)
        for row in source_payload_rows
    )
    row_sha = "0" * 64 if tamper_row_hash else sha256_text(row_jsonl)
    source_sha = sha256_text(source_jsonl)
    episode_payload = json.loads(episode.model_dump_json())
    episode_payload["blind_artifact_sha256"] = blind_hash
    episode_payload["blind_seal_receipt"] = {
        **episode_payload["blind_seal_receipt"],
        "run_id": "RUN-bundle-test",
        "trade_date": episode.trade_date.isoformat(),
        "cutoff_at": episode.cutoff_at.isoformat(),
        "blind_context_mode": blind_context_mode,
        "blind_artifact_sha256": blind_hash,
        "row_disposition_sha256": row_sha,
        "source_ledger_sha256": source_sha,
        "validation": {
            "blind_web_search_call_count": blind_web_search_call_count,
            "blind_price_repository_access_count": blind_price_repository_access_count,
            "blind_current_price_access_count": blind_current_price_access_count,
            "canonical_blind_hash_verified": True,
        },
    }
    if tamper_receipt_contract:
        episode_payload["blind_seal_receipt"]["blind_artifact_sha256"] = "0" * 64
    receipt_sha = sha256_text(_write_json_text(episode_payload["blind_seal_receipt"]))
    phase_state = phase_state_payload or {
        "schema_version": "nslab.phase_state.v1",
        "run_id": "RUN-bundle-test",
        "phase": "BLIND_SEALED",
        "completed_phases": [f"PHASE_A_{blind_context_mode}"],
        "trade_date": episode.trade_date.isoformat(),
        "cutoff_at": episode.cutoff_at.isoformat(),
        "sealed_at": episode.created_at.isoformat(),
        "blind_seal_receipt_sha256": receipt_sha,
    }
    if tamper_phase_contract:
        phase_state["completed_phases"] = ["PHASE_A_wrong"]
        phase_state["cutoff_at"] = "2030-01-10T09:00:00+09:00"
    phase_state_json = _write_json_text(phase_state)
    manifest_validation = {
        "markers_complete": True,
        "json_valid": True,
        "jsonl_valid": True,
        "blind_hash_verified": True,
        "blind_execution_guard_verified": True,
        "row_disposition_hash_verified": True,
        "row_disposition_coverage_verified": True,
        "source_ledger_hash_verified": True,
        "source_ledger_entry_count_verified": True,
        "research_episode_hash_verified": True,
        "brain_delta_hash_verified": True,
        "blind_seal_receipt_hash_verified": True,
        "blind_seal_receipt_contract_verified": True,
        "phase_state_hash_verified": True,
        "phase_state_contract_verified": True,
        "phase_state_receipt_link_verified": True,
        "id_reference_integrity_verified": True,
        "manifest_validation_self_consistent_verified": True,
    }
    manifest_validation.update(manifest_validation_overrides or {})
    manifest = {
        "schema_version": "nslab.bundle_manifest.v1",
        "run_id": "RUN-bundle-test",
        "trade_date": episode.trade_date.isoformat(),
        "cutoff_at": episode.cutoff_at.isoformat(),
        "blind_context_mode": blind_context_mode,
        "blind_web_search_call_count": blind_web_search_call_count,
        "blind_price_repository_access_count": blind_price_repository_access_count,
        "blind_current_price_access_count": blind_current_price_access_count,
        "no_d_outcome_exposed": no_d_outcome_exposed,
        "blind_artifact_sha256": blind_hash,
        "row_disposition_sha256": row_sha,
        "row_disposition_coverage_ratio": row_disposition_coverage_ratio,
        "source_ledger_sha256": source_sha,
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
        "validation": manifest_validation,
    }
    candidate_blocks = ""
    if include_candidate_verification:
        candidate_web_checks = json.dumps(
            {
                "schema_version": "nslab.candidate_web_check.v1",
                "run_id": "RUN-bundle-test",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "source_id": "WEB-CANDIDATE-1",
                "query": "candidate verification",
                "title": "candidate source",
                "url": "https://example.test/candidate",
                "source_url": "https://example.test/candidate",
                "published_at": "2030-01-10T08:30:00+09:00",
                "retrieved_at": "2030-01-10T08:31:00+09:00",
                "cutoff_at": episode.cutoff_at.isoformat(),
                "time_verified": True,
                "available_before_cutoff": True,
                "content_sha256": "candidate-hash",
            },
            ensure_ascii=False,
        )
        excluded_candidate_web_checks = json.dumps(
            {
                "schema_version": "nslab.excluded_candidate_web_check.v1",
                "run_id": "RUN-bundle-test",
                "candidate_rank": 1,
                "candidate_ticker": "UNKNOWN",
                "candidate_company_name": "CandidateCo",
                "candidate_path_type": "SINGLE_EVENT",
                "source_id": "WEB-CANDIDATE-EXCLUDED",
                "query": "candidate verification",
                "title": "excluded candidate source",
                "url": "https://example.test/excluded",
                "source_url": "https://example.test/excluded",
                "published_at": "2030-01-10T09:30:00+09:00",
                "retrieved_at": "2030-01-10T09:31:00+09:00",
                "cutoff_at": episode.cutoff_at.isoformat(),
                "exclusion_reason": "after_cutoff",
            },
            ensure_ascii=False,
        )
        candidate_verification = json.dumps(
            {
                "schema_version": "nslab.candidate_verification.v1",
                "run_id": "RUN-bundle-test",
                "created_at": episode.created_at.isoformat(),
                "cutoff_at": episode.cutoff_at.isoformat(),
                "required_dimensions": ["listed_security_and_exact_ticker"],
                "subject_count": 1,
                "findings": [
                    {
                        "subject_type": "final_candidate",
                        "candidate_rank": 1,
                        "candidate_ticker": "UNKNOWN",
                        "candidate_company_name": "CandidateCo",
                        "candidate_path_type": "SINGLE_EVENT",
                        "query": "candidate verification",
                        "source_count": 1,
                        "excluded_source_count": 1,
                        "accepted_source_ids": [
                            (
                                "WEB-CANDIDATE-OTHER"
                                if tamper_candidate_verification_contract
                                else "WEB-CANDIDATE-1"
                            )
                        ],
                        "excluded_source_ids": ["WEB-CANDIDATE-EXCLUDED"],
                        "verification_dimensions": [
                            {
                                "name": "listed_security_and_exact_ticker",
                                "status": "source_collected",
                                "evidence_source_ids": ["WEB-CANDIDATE-1"],
                                "notes": ["cutoff-safe source collected"],
                            }
                        ],
                        "d_minus_one_market_data_only": False,
                        "uncertainties": [],
                    }
                ],
                "notes": ["test verification"],
            },
            ensure_ascii=False,
        )
        manifest["candidate_web_check_sha256"] = sha256_text(candidate_web_checks)
        manifest["candidate_web_check_count"] = 1
        manifest["excluded_candidate_web_check_sha256"] = sha256_text(
            excluded_candidate_web_checks
        )
        manifest["excluded_candidate_web_check_count"] = 1
        manifest["candidate_verification_sha256"] = sha256_text(candidate_verification)
        manifest["candidate_verification_count"] = 1
        manifest_validation["candidate_web_check_hash_verified"] = True
        manifest_validation["candidate_web_check_count_verified"] = True
        manifest_validation["excluded_candidate_web_check_hash_verified"] = True
        manifest_validation["excluded_candidate_web_check_count_verified"] = True
        manifest_validation["candidate_verification_hash_verified"] = True
        manifest_validation["candidate_verification_count_verified"] = True
        manifest_validation["candidate_verification_contract_verified"] = True
        candidate_blocks = f"""
<!-- NSLAB:BEGIN candidate_web_checks.jsonl -->
```jsonl
{candidate_web_checks}
```
<!-- NSLAB:END candidate_web_checks.jsonl -->

<!-- NSLAB:BEGIN candidate_verification.json -->
```json
{candidate_verification}
```
<!-- NSLAB:END candidate_verification.json -->

<!-- NSLAB:BEGIN excluded_candidate_web_checks.jsonl -->
```jsonl
{excluded_candidate_web_checks}
```
<!-- NSLAB:END excluded_candidate_web_checks.jsonl -->

"""
    final_context_block = ""
    if include_final_synthesis_context:
        final_context_payload = _final_synthesis_payload()
        final_context_summary = final_synthesis_input_summary(final_context_payload)
        final_context = {
            "schema_version": "nslab.final_synthesis_context.v1",
            "run_id": "RUN-bundle-test",
            "prompt_version": "synthesis.final.v1",
            "required_inputs": (
                ["current_news"]
                if tamper_final_synthesis_context_contract
                else list(FINAL_SYNTHESIS_REQUIRED_INPUTS)
            ),
            "payload_sha256": sha256_text(canonical_json(final_context_payload)),
            "input_summary": final_context_summary,
            "payload": final_context_payload,
        }
        final_context_json = json.dumps(
            final_context,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        manifest["final_synthesis_context_sha256"] = sha256_text(final_context_json)
        manifest["final_synthesis_context_summary"] = final_context_summary
        manifest_validation["final_synthesis_context_hash_verified"] = True
        manifest_validation["final_synthesis_context_contract_verified"] = True
        final_context_block = f"""
<!-- NSLAB:BEGIN final_synthesis_context.json -->
```json
{final_context_json}
```
<!-- NSLAB:END final_synthesis_context.json -->

"""
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
{json.dumps(episode_payload, ensure_ascii=False, indent=2, sort_keys=True)}
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

{candidate_blocks}
{final_context_block}
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
    assert parsed.validation["blind_execution_guard_verified"]
    assert parsed.validation["row_disposition_hash_verified"]
    assert parsed.validation["row_disposition_coverage_verified"]
    assert parsed.validation["source_ledger_hash_verified"]
    assert parsed.validation["source_ledger_entry_count_verified"]
    assert parsed.validation["research_episode_hash_verified"]
    assert parsed.validation["brain_delta_hash_verified"]
    assert parsed.validation["blind_seal_receipt_hash_verified"]
    assert parsed.validation["blind_seal_receipt_contract_verified"]
    assert parsed.validation["phase_state_hash_verified"]
    assert parsed.validation["phase_state_contract_verified"]
    assert parsed.validation["phase_state_receipt_link_verified"]
    assert parsed.validation["id_reference_integrity_verified"]
    assert parsed.validation["manifest_validation_self_consistent_verified"]
    assert parsed.jsonl_blocks["row_disposition.jsonl"][0]["row_number"] == 1
    assert parsed.jsonl_blocks["brain_delta.jsonl"][0]["record_type"] == "memory_claim"
    assert imported.episode_id == episode.episode_id
    assert any(item.source_type == "nslab_markdown_bundle" for item in imported.provenance)
    assert (tmp_path / "research" / "episodes" / f"{episode.episode_id}.json").exists()
    assert len(list((tmp_path / "data" / "raw" / "research").glob("*.md"))) == 1


def test_bundle_import_accepts_final_synthesis_context_contract(tmp_path) -> None:
    source = tmp_path / "final_context_bundle.md"
    source.write_text(
        _bundle_text(_episode(), include_final_synthesis_context=True),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)
    imported = ResearchImporter(tmp_path).import_path(source, mode="bundle")

    assert parsed.validation["final_synthesis_context_hash_verified"]
    assert parsed.validation["final_synthesis_context_contract_verified"]
    assert imported.episode_id == _episode().episode_id


def test_bundle_import_rejects_final_synthesis_context_contract_mismatch(
    tmp_path,
) -> None:
    source = tmp_path / "bad_final_context_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            include_final_synthesis_context=True,
            tamper_final_synthesis_context_contract=True,
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert parsed.validation["final_synthesis_context_hash_verified"]
    assert not parsed.validation["final_synthesis_context_contract_verified"]
    with pytest.raises(BundleImportError, match="final_synthesis_context.json content"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_accepts_candidate_verification_contract(tmp_path) -> None:
    source = tmp_path / "candidate_verification_bundle.md"
    source.write_text(
        _bundle_text(_episode(), include_candidate_verification=True),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)
    imported = ResearchImporter(tmp_path).import_path(source, mode="bundle")

    assert parsed.validation["candidate_web_check_hash_verified"]
    assert parsed.validation["candidate_web_check_count_verified"]
    assert parsed.validation["excluded_candidate_web_check_hash_verified"]
    assert parsed.validation["excluded_candidate_web_check_count_verified"]
    assert parsed.validation["candidate_verification_hash_verified"]
    assert parsed.validation["candidate_verification_count_verified"]
    assert parsed.validation["candidate_verification_contract_verified"]
    assert imported.episode_id == _episode().episode_id


def test_bundle_import_rejects_candidate_verification_contract_mismatch(
    tmp_path,
) -> None:
    source = tmp_path / "bad_candidate_verification_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            include_candidate_verification=True,
            tamper_candidate_verification_contract=True,
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert parsed.validation["candidate_verification_hash_verified"]
    assert parsed.validation["candidate_verification_count_verified"]
    assert not parsed.validation["candidate_verification_contract_verified"]
    with pytest.raises(BundleImportError, match="candidate_verification.json content"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


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


def test_bundle_import_rejects_blind_execution_guard_violation(tmp_path) -> None:
    source = tmp_path / "blind_guard_violation_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            blind_web_search_call_count=1,
            no_d_outcome_exposed=False,
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert not parsed.validation["blind_execution_guard_verified"]
    with pytest.raises(BundleImportError, match="blind execution guard"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_accepts_cutoff_safe_web_blind_sources(tmp_path) -> None:
    episode = _episode().model_copy(
        update={
            "blind_integrity": {
                "blind_context_mode": "CUTOFF_SAFE_WEB_BLIND",
                "blind_web_search_call_count": 2,
                "blind_price_repository_access_count": 0,
                "blind_current_price_access_count": 0,
                "no_d_outcome_exposed": True,
            }
        }
    )
    source = tmp_path / "cutoff_safe_web_bundle.md"
    source.write_text(
        _bundle_text(
            episode,
            blind_context_mode="CUTOFF_SAFE_WEB_BLIND",
            blind_web_search_call_count=2,
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
                    "event_ids": ["EVT-1"],
                    "content_sha256": "abc",
                    "notes": "test source",
                },
                {
                    "source_id": "WEB-1",
                    "source_type": "web_search_result",
                    "title": "safe web source",
                    "publisher": None,
                    "url": "https://example.test/safe",
                    "published_at": "2030-01-10T08:30:00+09:00",
                    "retrieved_at": "2030-01-10T08:31:00+09:00",
                    "time_verified": True,
                    "available_before_cutoff": True,
                    "usage_phase": "BLIND",
                    "input_row_ids": [],
                    "event_ids": [],
                    "content_sha256": "def",
                    "notes": "cutoff-safe web source",
                },
            ],
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert parsed.validation["blind_execution_guard_verified"]
    assert parsed.validation["id_reference_integrity_verified"]


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


def test_bundle_import_rejects_blind_seal_receipt_contract_mismatch(tmp_path) -> None:
    source = tmp_path / "tampered_seal_contract_bundle.md"
    source.write_text(
        _bundle_text(_episode(), tamper_receipt_contract=True),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert parsed.validation["blind_seal_receipt_hash_verified"]
    assert not parsed.validation["blind_seal_receipt_contract_verified"]
    with pytest.raises(BundleImportError, match="blind_seal_receipt content"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_mismatched_phase_state_hash(tmp_path) -> None:
    source = tmp_path / "tampered_phase_bundle.md"
    source.write_text(_bundle_text(_episode(), tamper_phase_hash=True), encoding="utf-8")

    parsed = parse_bundle(source)

    assert not parsed.validation["phase_state_hash_verified"]
    with pytest.raises(BundleImportError, match="phase_state.json hash"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_import_rejects_phase_state_contract_mismatch(tmp_path) -> None:
    source = tmp_path / "tampered_phase_contract_bundle.md"
    source.write_text(
        _bundle_text(_episode(), tamper_phase_contract=True),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert parsed.validation["phase_state_hash_verified"]
    assert not parsed.validation["phase_state_contract_verified"]
    assert parsed.validation["phase_state_receipt_link_verified"]
    with pytest.raises(BundleImportError, match="phase_state.json content"):
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


def test_bundle_import_rejects_manifest_validation_mismatch(tmp_path) -> None:
    source = tmp_path / "bad_manifest_validation_bundle.md"
    source.write_text(
        _bundle_text(
            _episode(),
            manifest_validation_overrides={"blind_hash_verified": False},
        ),
        encoding="utf-8",
    )

    parsed = parse_bundle(source)

    assert not parsed.validation["manifest_validation_self_consistent_verified"]
    with pytest.raises(BundleImportError, match="validation does not match"):
        ResearchImporter(tmp_path).import_path(source, mode="bundle")


def test_bundle_source_ledger_rejects_missing_required_fields(tmp_path) -> None:
    source = tmp_path / "bad_source_ledger_bundle.md"
    source.write_text(
        _bundle_text(_episode(), source_rows=[{"source_id": "SRC-1"}]),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="source_ledger.jsonl:1 missing fields"):
        parse_bundle(source)


def test_bundle_source_ledger_rejects_source_url_mismatch(tmp_path) -> None:
    source = tmp_path / "bad_source_url_bundle.md"
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
                    "source_url": "file://news.csv#row=2",
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
            ],
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="source_ledger.jsonl:1 source_url mismatch"):
        parse_bundle(source)


def test_bundle_source_ledger_rejects_blind_source_after_cutoff_timestamp(tmp_path) -> None:
    source = tmp_path / "after_cutoff_source_ledger_bundle.md"
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
                    "source_url": "file://news.csv#row=1",
                    "published_at": "2030-01-10T09:30:00+09:00",
                    "retrieved_at": "2030-01-10T09:30:01+09:00",
                    "time_verified": True,
                    "available_before_cutoff": True,
                    "usage_phase": "BLIND",
                    "input_row_ids": [1],
                    "event_ids": ["EVT-1"],
                    "content_sha256": "abc",
                    "notes": "misflagged after-cutoff source",
                }
            ],
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="source_ledger.jsonl:1 BLIND source after cutoff"):
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


def test_bundle_candidate_web_checks_reject_opened_text_duplication(tmp_path) -> None:
    candidate_jsonl = json.dumps(
        {
            "schema_version": "nslab.candidate_web_check.v1",
            "run_id": "RUN-bundle-test",
            "candidate_rank": 1,
            "candidate_ticker": "UNKNOWN",
            "candidate_company_name": "CandidateCo",
            "candidate_path_type": "SINGLE_EVENT",
            "source_id": "WEB-CANDIDATE-1",
            "query": "candidate verification",
            "title": "candidate source",
            "url": "https://example.test/candidate",
            "source_url": "https://example.test/candidate",
            "published_at": "2030-01-10T08:30:00+09:00",
            "retrieved_at": "2030-01-10T08:31:00+09:00",
            "cutoff_at": "2030-01-10T08:59:59+09:00",
            "time_verified": True,
            "available_before_cutoff": True,
            "content_sha256": "abc",
            "opened_text": "raw text must stay outside the bundle",
        },
        ensure_ascii=False,
    )
    candidate_block = f"""
<!-- NSLAB:BEGIN candidate_web_checks.jsonl -->
```jsonl
{candidate_jsonl}
```
<!-- NSLAB:END candidate_web_checks.jsonl -->
"""
    source = tmp_path / "duplicated_candidate_web_bundle.md"
    source.write_text(
        _bundle_text(_episode()).replace(
            "<!-- NSLAB:BEGIN phase_state.json -->",
            f"{candidate_block}\n<!-- NSLAB:BEGIN phase_state.json -->",
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleImportError, match="must not duplicate opened/body/content"):
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
