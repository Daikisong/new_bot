"""Content hashes that bind the complete brain-record envelope."""

from __future__ import annotations

from collections.abc import Iterable

from news_scalping_lab.records.models import BrainRecordEnvelope
from news_scalping_lab.records.routing import record_routing_metadata
from news_scalping_lab.utils import canonical_json, sha256_text


def brain_record_envelope_sha256(record: BrainRecordEnvelope) -> str:
    return sha256_text(canonical_json(record.model_dump(mode="json")))


def brain_record_envelope_hashes(
    records: Iterable[BrainRecordEnvelope],
) -> dict[str, str]:
    return {record.record_id: brain_record_envelope_sha256(record) for record in records}


def brain_record_routing_root_sha256(
    records: Iterable[BrainRecordEnvelope],
) -> str:
    return sha256_text(
        canonical_json(
            {
                record.record_id: record_routing_metadata(record).model_dump(mode="json")
                for record in sorted(records, key=lambda item: item.record_id)
            }
        )
    )
