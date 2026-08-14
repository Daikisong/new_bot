"""Cross-cutting production evidence and fallback policies."""

from __future__ import annotations

from enum import StrEnum


class EvidencePolicy(StrEnum):
    CSV_MEMORY_ONLY_STRICT = "csv-memory-only-strict"
    POSTCLOSE_WEB_AUDIT_OPTIONAL = "postclose-web-audit-optional"

    @classmethod
    def parse(cls, value: str | EvidencePolicy) -> EvidencePolicy:
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower().replace("_", "-")
        return cls(normalized)


class EmbeddingFallbackPolicy(StrEnum):
    ALLOW_DETERMINISTIC_FALLBACK = "allow-deterministic-fallback"
    FAIL_CLOSED = "fail-closed"

    @classmethod
    def parse(
        cls,
        value: str | EmbeddingFallbackPolicy,
    ) -> EmbeddingFallbackPolicy:
        if isinstance(value, cls):
            return value
        normalized = value.strip().lower().replace("_", "-")
        return cls(normalized)


def web_required_for_policy(policy: EvidencePolicy | str) -> bool:
    EvidencePolicy.parse(policy)
    return False
