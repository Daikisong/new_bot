"""DuckDB/Parquet warehouse projections.

The warehouse is a derived, reproducible projection of canonical JSON data. It is
not the source of truth for research memory; source files remain immutable under
`research/`, `memory/`, and `brain/`.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import ValidationError

from news_scalping_lab.contracts.models import (
    BlindPrediction,
    CompanyMemory,
    MechanismMemory,
    ResearchEpisode,
)
from news_scalping_lab.diagnostic_reports import write_diagnostic_report
from news_scalping_lab.memory.company import CompanyMemoryStore
from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.store import (
    BrainRecordStore,
    audit_record_store,
    record_store_report_payload,
)
from news_scalping_lab.storage import ResearchStore
from news_scalping_lab.utils import read_json

EXPECTED_WAREHOUSE_FILES = (
    "events.parquet",
    "event_sources.parquet",
    "event_ticker_edges.parquet",
    "research_episodes.parquet",
    "daily_outcomes.parquet",
    "predictions.parquet",
    "market_memory.parquet",
    "mechanism_memory.parquet",
    "company_memory.parquet",
    "brain_records.parquet",
    "issuer_day_cases.parquet",
    "direct_event_cases.parquet",
    "theme_formation_cases.parquet",
    "beneficiary_cases.parquet",
    "leader_pairs.parquet",
    "error_cases.parquet",
    "memory_claims.parquet",
    "research_questions.parquet",
    "record_provenance.parquet",
    "record_coverage.parquet",
)

RecordTypeFilter = str | tuple[str, ...] | list[str] | set[str]

RECORD_COVERAGE_COLUMNS = (
    "episode_id",
    "record_type",
    "evidence_phase",
    "training_target",
    "record_count",
    "training_eligible_record_count",
    "ineligible_record_count",
    "audit_only_record_count",
)

_FILTER_VALUE_KEYS = {
    "ticker": "tickers_json",
    "company_name": "company_names_json",
    "theme_id": "theme_ids_json",
    "path_type": "path_types_json",
    "response_class": "response_classes_json",
}

_PAYLOAD_FILTER_ALIASES: dict[str, tuple[str, ...]] = {
    "ticker": (
        "ticker",
        "candidate_ticker",
        "outcome_ticker",
        "chosen_leader_ticker",
        "rejected_candidate_tickers",
        "peer_universe",
        "blind_preferred_ticker",
        "blind_rejected_ticker",
        "outcome_winner_ticker",
        "missed_ticker",
        "corrected_ticker",
        "issuer_ticker",
    ),
    "company_name": (
        "company_name",
        "entity_name",
        "name",
        "name_on_D",
        "candidate_company_name",
        "outcome_company_name",
        "chosen_leader_company_name",
        "blind_preferred_company_name",
        "blind_rejected_company_name",
        "outcome_winner_company_name",
        "missed_company_name",
        "corrected_company_name",
        "company_name_on_D",
        "issuer_company_name",
    ),
    "theme_id": (
        "theme_id",
        "theme_ids",
        "candidate_theme_id",
        "missed_theme_ids",
        "missed_theme_id",
        "corrected_theme_ids",
        "corrected_theme_id",
    ),
    "path_type": (
        "path_type",
        "candidate_lane",
        "candidate_path_type",
        "missed_path_type",
        "corrected_path_type",
    ),
    "response_class": (
        "response_class",
        "candidate_response_class",
        "blind_response_class",
        "outcome_response_class",
    ),
}

_PAYLOAD_NESTED_FILTER_ALIASES: dict[str, tuple[tuple[str, str], ...]] = {
    "ticker": (
        ("D_outcome", "ticker"),
        ("D_outcome", "code"),
        ("outcome", "ticker"),
        ("outcome", "code"),
        ("issuer_day_outcome", "ticker"),
        ("issuer_day_outcome", "code"),
    ),
    "company_name": (
        ("D_outcome", "company_name"),
        ("D_outcome", "company_name_on_D"),
        ("D_outcome", "name"),
        ("outcome", "company_name"),
        ("outcome", "company_name_on_D"),
        ("outcome", "name"),
        ("issuer_day_outcome", "company_name"),
        ("issuer_day_outcome", "company_name_on_D"),
        ("issuer_day_outcome", "name"),
    ),
    "response_class": (
        ("D_outcome", "response_class"),
        ("outcome", "response_class"),
        ("issuer_day_outcome", "response_class"),
    ),
}


class WarehouseStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.dir = root / "warehouse"
        self.dir.mkdir(parents=True, exist_ok=True)

    def rebuild_all(self) -> dict[str, int]:
        store = ResearchStore(self.root)
        records = BrainRecordStore(self.root).list_records()
        episodes, accepted_store_findings = _read_accepted_episodes_for_warehouse(
            store,
            records=records,
        )
        company_delta_records = [
            record for record in records if record.record_type == "company_memory_delta"
        ]
        company_delta_result = CompanyMemoryStore(self.root).apply_record_delta_records(
            company_delta_records
        )
        if company_delta_result.skipped_invalid_record_ids:
            invalid_ids = ", ".join(company_delta_result.skipped_invalid_record_ids)
            raise ValueError(f"invalid company_memory_delta records skipped: {invalid_ids}")
        counts = {
            "events": self.write_events(episodes),
            "event_sources": self.write_event_sources(episodes),
            "research_episodes": self.write_research_episodes(episodes),
            "event_ticker_edges": self.write_event_ticker_edges(episodes, records),
            "market_memory": self.write_market_memory(episodes),
            "mechanism_memory": self.write_mechanism_memory_from_files(),
            "company_memory": self.write_company_memory_from_files(),
            "predictions": self.write_predictions_from_files(),
            "daily_outcomes": self.write_daily_outcomes_from_files(),
            "company_memory_delta_records": company_delta_result.processed_record_count,
            "company_memory_delta_written": company_delta_result.written_count,
        }
        counts.update(
            {
                "brain_records": self.write_brain_records(records),
                "issuer_day_cases": self.write_issuer_day_cases(records),
                "direct_event_cases": self.write_direct_event_cases(records),
                "theme_formation_cases": self.write_theme_formation_cases(records),
                "beneficiary_cases": self.write_beneficiary_cases(records),
                "leader_pairs": self.write_leader_pairs(records),
                "error_cases": self.write_error_cases(records),
                "memory_claims": self.write_memory_claim_records(records),
                "research_questions": self.write_research_questions(records),
                "record_provenance": self.write_record_provenance(records),
                "record_coverage": self.write_record_coverage(records),
            }
        )
        record_store_audit = audit_record_store(self.root, deep=True)
        report_payload = record_store_report_payload(
            self.root,
            record_store_audit,
            warehouse_counts=counts,
        )
        if accepted_store_findings:
            report_payload["warehouse_rebuild_findings"] = accepted_store_findings
        write_diagnostic_report(
            self.root,
            "brain_record_store_report",
            report_payload,
        )
        return counts

    def write_events(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            for event in episode.observed_events:
                rows.append(
                    {
                        "event_id": event.event_id,
                        "episode_id": episode.episode_id,
                        "trade_date": episode.trade_date.isoformat(),
                        "published_at": event.published_at.isoformat(),
                        "row_number": event.row_number,
                        "title": event.title,
                        "body": event.body,
                        "source_id": event.source_id,
                        "provenance_json": _json(event.provenance),
                    }
                )
        self._write_rows("events.parquet", rows)
        return len(rows)

    def write_event_sources(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for episode in episodes:
            for event in episode.observed_events:
                for source in event.provenance:
                    key = (episode.episode_id, event.event_id, source.source_id, source.uri)
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "source_id": source.source_id,
                            "event_id": event.event_id,
                            "episode_id": episode.episode_id,
                            "source_type": source.source_type,
                            "uri": source.uri,
                            "content_sha256": source.content_sha256,
                            "excerpt": source.excerpt,
                            "observed_at": (
                                source.observed_at.isoformat() if source.observed_at else None
                            ),
                        }
                    )
        self._write_rows("event_sources.parquet", rows)
        return len(rows)

    def write_research_episodes(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            rows.append(
                {
                    "episode_id": episode.episode_id,
                    "trade_date": episode.trade_date.isoformat(),
                    "cutoff_at": episode.cutoff_at.isoformat(),
                    "available_from": episode.available_from.isoformat(),
                    "execution_protocol_version": episode.execution_protocol_version,
                    "outcome_coverage_status": episode.outcome_coverage_status,
                    "research_version": episode.research_version,
                    "lesson_count": len(episode.lessons),
                    "counterexample_count": len(episode.counterexamples),
                    "candidate_count": len(episode.blind_predictions),
                    "eligibility_matrix_json": _json(episode.eligibility_matrix),
                    "provenance_json": _json(episode.provenance),
                }
            )
        self._write_rows("research_episodes.parquet", rows)
        return len(rows)

    def write_event_ticker_edges(
        self,
        episodes: list[ResearchEpisode],
        records: list[BrainRecordEnvelope] | None = None,
    ) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            for edge in episode.event_ticker_edges:
                rows.append(
                    {
                        "source_kind": "accepted_episode_edge",
                        "record_id": None,
                        "edge_id": edge.edge_id,
                        "episode_id": edge.episode_id,
                        "event_id": edge.event_id,
                        "ticker": edge.ticker,
                        "company_name": edge.company_name,
                        "relation_class": str(edge.relation_class),
                        "confidence_label": str(edge.confidence_label),
                        "directly_mentioned": edge.directly_mentioned,
                        "provenance_json": _json(edge.provenance),
                    }
                )
        for record in records or []:
            if record.record_type != "event_ticker_edge":
                continue
            edge_id = record.payload.get("edge_id")
            rows.append(
                {
                    "source_kind": "brain_record_edge",
                    "record_id": record.record_id,
                    "edge_id": edge_id if isinstance(edge_id, str) and edge_id else record.record_id,
                    "episode_id": record.episode_id,
                    "event_id": record.payload.get("event_id"),
                    "ticker": record.payload.get("ticker"),
                    "company_name": record.payload.get("company_name"),
                    "relation_class": record.payload.get("relation_class"),
                    "confidence_label": record.confidence_label,
                    "directly_mentioned": record.payload.get("directly_mentioned"),
                    "provenance_json": _json(record.provenance_source_ids),
                }
            )
        self._write_rows("event_ticker_edges.parquet", rows)
        return len(rows)

    def write_market_memory(self, episodes: list[ResearchEpisode]) -> int:
        rows: list[dict[str, Any]] = []
        for episode in episodes:
            for claim in [*episode.lessons, *episode.counterexamples]:
                rows.append(
                    {
                        "claim_id": claim.claim_id,
                        "episode_id": episode.episode_id,
                        "trade_date": episode.trade_date.isoformat(),
                        "available_from": claim.available_from.isoformat(),
                        "status": str(claim.status),
                        "confidence_label": str(claim.confidence_label),
                        "statement": claim.statement,
                        "mechanism": claim.mechanism,
                        "support_episode_ids_json": _json(claim.support_episode_ids),
                        "contradiction_episode_ids_json": _json(claim.contradiction_episode_ids),
                    }
                )
        self._write_rows("market_memory.parquet", rows)
        return len(rows)

    def write_mechanism_memory_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        path = self.root / "memory" / "mechanisms" / "current" / "mechanisms.jsonl"
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                memory = MechanismMemory.model_validate(json.loads(line))
                rows.append(
                    {
                        "mechanism_id": memory.mechanism_id,
                        "natural_language_description": memory.natural_language_description,
                        "causal_chain_json": _json(memory.causal_chain),
                        "observed_variations_json": _json(memory.observed_variations),
                        "successful_cases_json": _json(memory.successful_cases),
                        "failed_cases_json": _json(memory.failed_cases),
                        "boundary_conditions_json": _json(memory.boundary_conditions),
                        "leader_selection_notes_json": _json(memory.leader_selection_notes),
                        "provenance_json": _json(memory.provenance),
                    }
                )
        self._write_rows("mechanism_memory.parquet", rows)
        return len(rows)

    def write_company_memory_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "memory" / "company_memory").glob("*.json")):
            memory = CompanyMemory.model_validate(read_json(path))
            rows.append(
                {
                    "ticker": memory.ticker,
                    "company_name": memory.company_name,
                    "known_at": memory.known_at.isoformat(),
                    "aliases_json": _json(memory.aliases),
                    "business_descriptions_json": _json(memory.business_descriptions),
                    "locations_json": _json(memory.locations),
                    "customers_json": _json(memory.customers),
                    "supply_chain_roles_json": _json(memory.supply_chain_roles),
                    "prior_market_narratives_json": _json(memory.prior_market_narratives),
                    "prior_leader_occurrences_json": _json(memory.prior_leader_occurrences),
                    "contradictory_relations_json": _json(memory.contradictory_relations),
                    "provenance_json": _json(memory.provenance),
                }
            )
        self._write_rows("company_memory.parquet", rows)
        return len(rows)

    def write_prediction(self, prediction: BlindPrediction) -> None:
        rows = [
            {
                "prediction_id": prediction.prediction_id,
                "trade_date": prediction.trade_date.isoformat(),
                "cutoff_at": prediction.cutoff_at.isoformat(),
                "sealed_at": prediction.sealed_at.isoformat() if prediction.sealed_at else None,
                "blind_artifact_sha256": prediction.blind_artifact_sha256,
                "candidate_count": len(prediction.candidates),
                "dominant_sector_count": len(prediction.dominant_sectors),
            }
        ]
        self._append_or_replace_by_key("predictions.parquet", rows, "prediction_id")

    def write_predictions_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "predictions").glob("*.json")):
            data = read_json(path)
            rows.append(
                {
                    "prediction_id": data.get("prediction_id"),
                    "trade_date": data.get("trade_date"),
                    "cutoff_at": data.get("cutoff_at"),
                    "sealed_at": data.get("sealed_at"),
                    "blind_artifact_sha256": data.get("blind_artifact_sha256"),
                    "candidate_count": len(data.get("candidates", [])),
                    "dominant_sector_count": len(data.get("dominant_sectors", [])),
                }
            )
        self._write_rows("predictions.parquet", rows)
        return len(rows)

    def write_daily_outcomes_from_files(self) -> int:
        rows: list[dict[str, Any]] = []
        for path in sorted((self.root / "reports").glob("*_postmortem.json")):
            data = read_json(path)
            rows.append(
                {
                    "schema_version": data.get("schema_version"),
                    "execution_protocol_version": data.get("execution_protocol_version"),
                    "trade_date": data.get("trade_date"),
                    "blind_prediction_id": data.get("blind_prediction_id"),
                    "blind_prediction_sha256": data.get("blind_prediction_sha256"),
                    "created_at": data.get("created_at"),
                    "outcome_count": len(data.get("outcomes", {})),
                    "outcome_coverage_status": data.get("outcome_coverage_status"),
                    "outcomes_json": _json(data.get("outcomes", {})),
                    "performance_metrics_json": _json(data.get("performance_metrics", {})),
                    "postmortem_json": _json(data.get("postmortem", {})),
                    "eligibility_matrix_json": _json(data.get("eligibility_matrix", {})),
                }
            )
        self._write_rows("daily_outcomes.parquet", rows)
        return len(rows)

    def write_brain_records(self, records: list[BrainRecordEnvelope]) -> int:
        rows: list[dict[str, Any]] = []
        for record in records:
            filter_values = _brain_record_filter_values(record.payload)
            rows.append(
                {
                    "record_id": record.record_id,
                    "record_type": record.record_type,
                    "episode_id": record.episode_id,
                    "trade_date": record.trade_date.isoformat(),
                    "available_from": record.available_from.isoformat(),
                    "training_target": record.training_target,
                    "evidence_phase": record.evidence_phase,
                    "training_eligible": record.training_eligible,
                    "eligibility_reason": record.eligibility_reason,
                    "status": record.status,
                    "confidence_label": record.confidence_label,
                    "typed_payload_status": record.typed_payload_status,
                    "raw_payload_sha256": record.raw_payload_sha256,
                    "normalized_payload_sha256": record.normalized_payload_sha256,
                    "ticker": _first_filter_value(filter_values["ticker"]),
                    "company_name": _first_filter_value(filter_values["company_name"]),
                    "theme_id": _first_filter_value(filter_values["theme_id"]),
                    "path_type": _first_filter_value(filter_values["path_type"]),
                    "response_class": _first_filter_value(filter_values["response_class"]),
                    "tickers_json": _json(filter_values["ticker"]),
                    "company_names_json": _json(filter_values["company_name"]),
                    "theme_ids_json": _json(filter_values["theme_id"]),
                    "path_types_json": _json(filter_values["path_type"]),
                    "response_classes_json": _json(filter_values["response_class"]),
                    "payload_json": _json(record.payload),
                }
            )
        self._write_rows("brain_records.parquet", rows)
        return len(rows)

    def write_issuer_day_cases(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(
                record,
                extra_fields=(
                    "issuer_day_case_id",
                    "issuer_day_weight_group_id",
                    "ticker",
                    "company_name",
                    "response_class",
                    "attribution_status",
                    "sample_weight",
                ),
            )
            for record in records
            if record.record_type == "supervised_issuer_day_case"
        ]
        self._write_rows("issuer_day_cases.parquet", rows)
        return len(rows)

    def write_direct_event_cases(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(
                record,
                extra_fields=(
                    "case_id",
                    "issuer_day_case_id",
                    "issuer_day_weight_group_id",
                    "ticker",
                    "company_name",
                    "event_id",
                    "observation_id",
                    "candidate_decision",
                    "response_class",
                    "sample_weight",
                ),
            )
            for record in records
            if record.record_type == "supervised_direct_event_case"
        ]
        self._write_rows("direct_event_cases.parquet", rows)
        return len(rows)

    def write_theme_formation_cases(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(
                record,
                extra_fields=(
                    "theme_id",
                    "theme_name",
                    "event_ids",
                    "observation_ids",
                    "fact_ids",
                    "inference_ids",
                    "peer_universe",
                    "chosen_leader_ticker",
                    "chosen_leader_company_name",
                    "rejected_candidate_tickers",
                    "response_class",
                    "sample_weight",
                    "label_quality",
                    "attribution_status",
                ),
            )
            for record in records
            if record.record_type in {"supervised_theme_formation_case", "theme_formation_case"}
        ]
        self._write_rows("theme_formation_cases.parquet", rows)
        return len(rows)

    def write_beneficiary_cases(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(
                record,
                extra_fields=(
                    "case_id",
                    "event_id",
                    "theme_id",
                    "candidate_ticker",
                    "candidate_company_name",
                    "candidate_path_type",
                    "beneficiary_relation",
                    "beneficiary_relation_evidence",
                    "blind_candidate_ids",
                    "outcome_ticker",
                    "outcome_company_name",
                    "correction_mode",
                    "sample_weight",
                ),
            )
            for record in records
            if record.record_type == "beneficiary_discovery_case"
        ]
        self._write_rows("beneficiary_cases.parquet", rows)
        return len(rows)

    def write_leader_pairs(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(
                record,
                extra_fields=(
                    "blind_pair_id",
                    "theme_id",
                    "blind_preferred_candidate_id",
                    "blind_rejected_candidate_id",
                    "outcome_preferred_candidate_id",
                    "blind_preferred_ticker",
                    "blind_rejected_ticker",
                    "outcome_winner_ticker",
                    "training_example_type",
                    "blind_preference_correct",
                ),
            )
            for record in records
            if record.record_type == "blind_leader_preference_pair"
        ]
        self._write_rows("leader_pairs.parquet", rows)
        return len(rows)

    def write_error_cases(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(record, extra_fields=("error_id", "error_type", "correction_mode"))
            for record in records
            if record.record_type.endswith("_error_case")
        ]
        self._write_rows("error_cases.parquet", rows)
        return len(rows)

    def write_memory_claim_records(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(
                record,
                extra_fields=("claim_id", "mechanism_id", "counterexample_id", "statement"),
            )
            for record in records
            if record.record_type in {"memory_claim", "mechanism_memory", "counterexample"}
        ]
        self._write_rows("memory_claims.parquet", rows)
        return len(rows)

    def write_research_questions(self, records: list[BrainRecordEnvelope]) -> int:
        rows = [
            _record_case_row(record, extra_fields=("question_id", "question", "priority"))
            for record in records
            if record.record_type == "research_question"
        ]
        self._write_rows("research_questions.parquet", rows)
        return len(rows)

    def write_record_provenance(self, records: list[BrainRecordEnvelope]) -> int:
        rows: list[dict[str, Any]] = []
        for record in records:
            for source_id in record.provenance_source_ids:
                rows.append(
                    {
                        "record_id": record.record_id,
                        "episode_id": record.episode_id,
                        "record_type": record.record_type,
                        "source_id": source_id,
                    }
                )
        self._write_rows("record_provenance.parquet", rows)
        return len(rows)

    def write_record_coverage(self, records: list[BrainRecordEnvelope]) -> int:
        grouped: dict[tuple[str, str, str, str], list[BrainRecordEnvelope]] = {}
        for record in records:
            grouped.setdefault(
                (
                    record.episode_id,
                    record.record_type,
                    record.evidence_phase,
                    record.training_target or "UNKNOWN",
                ),
                [],
            ).append(record)
        rows = [
            {
                "episode_id": episode_id,
                "record_type": record_type,
                "evidence_phase": evidence_phase,
                "training_target": training_target,
                "record_count": len(group_records),
                "training_eligible_record_count": sum(
                    1 for record in group_records if record.training_eligible
                ),
                "ineligible_record_count": sum(
                    1 for record in group_records if not record.training_eligible
                ),
                "audit_only_record_count": sum(
                    1 for record in group_records if record.evidence_phase == "AUDIT"
                ),
            }
            for (
                episode_id,
                record_type,
                evidence_phase,
                training_target,
            ), group_records in sorted(grouped.items())
        ]
        self._write_rows_with_schema("record_coverage.parquet", rows, RECORD_COVERAGE_COLUMNS)
        return len(rows)

    def write_empty(self, filename: str) -> None:
        self._write_rows(filename, [])

    def counts(self) -> dict[str, int | str]:
        result: dict[str, int | str] = {}
        for path in sorted(self.dir.glob("*.parquet")):
            try:
                with duckdb.connect(database=":memory:") as connection:
                    count = connection.sql(
                        f"select count(*) from read_parquet('{path.as_posix()}')"
                    ).fetchone()
                    result[path.name] = int(count[0]) if count else 0
            except duckdb.Error as exc:
                result[path.name] = f"ERROR: {exc}"
        return result

    def query_brain_records(
        self,
        *,
        record_type: RecordTypeFilter | None = None,
        training_target: str | None = None,
        evidence_phase: str | None = None,
        ticker: str | None = None,
        company_name: str | None = None,
        theme_id: str | None = None,
        path_type: str | None = None,
        response_class: str | None = None,
        training_eligible: bool | None = None,
        confidence_label: str | None = None,
        trade_date_from: str | None = None,
        trade_date_to: str | None = None,
        available_from_as_of: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit must be positive")
        parquet_path = self.dir / "brain_records.parquet"
        if not parquet_path.exists():
            raise FileNotFoundError(
                "warehouse brain_records.parquet not found; run warehouse rebuild"
            )
        if _parquet_has_only_empty_marker(parquet_path):
            return []
        sql_path = parquet_path.as_posix().replace("'", "''")
        where = ["1 = 1"]
        params: list[Any] = []

        def add_string_filter(column: str, value: str | None) -> None:
            if value is None:
                return
            where.append(f"{column} = ?")
            params.append(value)

        def add_record_type_filter(value: RecordTypeFilter | None) -> None:
            if value is None:
                return
            if isinstance(value, str):
                where.append("record_type = ?")
                params.append(value)
                return
            values = sorted({item for item in value if item})
            if not values:
                return
            placeholders = ", ".join("?" for _ in values)
            where.append(f"record_type in ({placeholders})")
            params.extend(values)

        add_record_type_filter(record_type)
        add_string_filter("training_target", training_target)
        add_string_filter("evidence_phase", evidence_phase)
        add_string_filter("confidence_label", confidence_label)
        if training_eligible is not None:
            where.append("training_eligible = ?")
            params.append(training_eligible)
        if trade_date_from is not None:
            where.append("trade_date >= ?")
            params.append(trade_date_from)
        if trade_date_to is not None:
            where.append("trade_date <= ?")
            params.append(trade_date_to)
        if available_from_as_of is not None:
            where.append("cast(available_from as TIMESTAMPTZ) <= cast(? as TIMESTAMPTZ)")
            params.append(available_from_as_of)
        query = f"""
            select
                record_id,
                record_type,
                episode_id,
                trade_date,
                available_from,
                training_target,
                evidence_phase,
                training_eligible,
                status,
                confidence_label,
                ticker,
                company_name,
                theme_id,
                path_type,
                response_class,
                tickers_json,
                company_names_json,
                theme_ids_json,
                path_types_json,
                response_classes_json,
                payload_json
            from read_parquet('{sql_path}')
            where {' and '.join(where)}
            order by trade_date, record_id
        """
        try:
            with duckdb.connect(database=":memory:") as connection:
                rows = connection.execute(query, params).fetchall()
                columns = [description[0] for description in connection.description]
        except duckdb.Error as exc:
            raise ValueError(f"warehouse brain record query failed: {exc}") from exc
        results: list[dict[str, Any]] = []
        for row in rows:
            result = dict(zip(columns, row, strict=True))
            payload_json = result.pop("payload_json", "{}")
            filter_columns = {
                key: result.pop(value_key, "[]")
                for key, value_key in _FILTER_VALUE_KEYS.items()
            }
            if not _matches_row_filters(
                result,
                filter_columns=filter_columns,
                ticker=ticker,
                company_name=company_name,
                theme_id=theme_id,
                path_type=path_type,
                response_class=response_class,
            ):
                continue
            result["payload"] = _loads_json_object(payload_json)
            results.append(result)
            if len(results) >= limit:
                break
        return results

    def _append_or_replace_by_key(self, filename: str, rows: list[dict[str, Any]], key: str) -> None:
        path = self.dir / filename
        existing = _read_rows(path) if path.exists() else []
        incoming_keys = {row[key] for row in rows}
        merged = [row for row in existing if row.get(key) not in incoming_keys]
        merged.extend(rows)
        self._write_rows(filename, merged)

    def _write_rows(self, filename: str, rows: list[dict[str, Any]]) -> None:
        path = self.dir / filename
        if not rows:
            table = pa.Table.from_arrays(
                [pa.array([], type=pa.string())],
                names=["_empty"],
            )
            pq.write_table(table, path)  # type: ignore[no-untyped-call]
            return
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, path)  # type: ignore[no-untyped-call]

    def _write_rows_with_schema(
        self,
        filename: str,
        rows: list[dict[str, Any]],
        columns: tuple[str, ...],
    ) -> None:
        if rows:
            self._write_rows(filename, rows)
            return
        path = self.dir / filename
        table = pa.Table.from_arrays(
            [pa.array([], type=pa.string()) for _ in columns],
            names=list(columns),
        )
        pq.write_table(table, path)  # type: ignore[no-untyped-call]


def _read_accepted_episodes_for_warehouse(
    store: ResearchStore,
    *,
    records: list[BrainRecordEnvelope],
) -> tuple[list[ResearchEpisode], list[str]]:
    try:
        return store.list_accepted(), []
    except (
        OSError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        ValidationError,
        TypeError,
        ValueError,
    ):
        if records:
            return [], ["accepted episode store is unreadable"]
        raise


def _read_rows(path: Path) -> list[dict[str, Any]]:
    table = pq.read_table(path)  # type: ignore[no-untyped-call]
    rows = cast(list[dict[str, Any]], table.to_pylist())
    if not rows:
        return []
    if set(rows[0]) == {"_empty"}:
        return []
    return rows


def _loads_json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parquet_has_only_empty_marker(path: Path) -> bool:
    try:
        table = pq.read_table(path)  # type: ignore[no-untyped-call]
    except (OSError, pa.ArrowInvalid):
        return False
    return bool(table.num_columns == 1 and table.column_names == ["_empty"])


def _json(value: Any) -> str:
    if isinstance(value, list):
        return json.dumps(
            [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value],
            ensure_ascii=False,
            sort_keys=True,
        )
    if hasattr(value, "model_dump"):
        return json.dumps(value.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _brain_record_filter_values(payload: dict[str, Any]) -> dict[str, list[str]]:
    values: dict[str, list[str]] = {}
    for field_name, aliases in _PAYLOAD_FILTER_ALIASES.items():
        collected: list[str] = []
        for alias in aliases:
            collected.extend(_string_values(payload.get(alias)))
        for parent_key, child_key in _PAYLOAD_NESTED_FILTER_ALIASES.get(field_name, ()):
            parent = payload.get(parent_key)
            if isinstance(parent, dict):
                collected.extend(_string_values(parent.get(child_key)))
        values[field_name] = _unique_strings(collected)
    return values


def _matches_row_filters(
    result: dict[str, Any],
    *,
    filter_columns: dict[str, object],
    ticker: str | None,
    company_name: str | None,
    theme_id: str | None,
    path_type: str | None,
    response_class: str | None,
) -> bool:
    return (
        _matches_filter_value(result, filter_columns, "ticker", ticker)
        and _matches_filter_value(result, filter_columns, "company_name", company_name)
        and _matches_filter_value(result, filter_columns, "theme_id", theme_id)
        and _matches_filter_value(result, filter_columns, "path_type", path_type)
        and _matches_filter_value(result, filter_columns, "response_class", response_class)
    )


def _matches_filter_value(
    result: dict[str, Any],
    filter_columns: dict[str, object],
    field_name: str,
    expected: str | None,
) -> bool:
    if expected is None:
        return True
    values = _string_values(result.get(field_name))
    values.extend(_string_values(_loads_json_list(filter_columns.get(field_name))))
    return expected in set(values)


def _loads_json_list(value: object) -> list[Any]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _first_filter_value(values: list[str]) -> str | None:
    return values[0] if values else None


def _string_values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, int | float) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, list | tuple | set):
        values: list[str] = []
        for item in value:
            values.extend(_string_values(item))
        return values
    return []


def _unique_strings(values: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            unique.append(value)
            seen.add(value)
    return unique


def _record_case_row(
    record: BrainRecordEnvelope,
    *,
    extra_fields: tuple[str, ...],
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "record_id": record.record_id,
        "episode_id": record.episode_id,
        "trade_date": record.trade_date.isoformat(),
        "available_from": record.available_from.isoformat(),
        "record_type": record.record_type,
        "training_target": record.training_target,
        "training_eligible": record.training_eligible,
        "payload_json": _json(record.payload),
    }
    for field_name in extra_fields:
        value = record.payload.get(field_name)
        row[field_name] = _json(value) if isinstance(value, list | dict) else value
    return row


def previous_trade_day(trade_day: date) -> date:
    return date.fromordinal(trade_day.toordinal() - 1)
