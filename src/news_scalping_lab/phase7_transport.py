"""Authenticated transport commitment for bounded Phase 7 bundle artifacts."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from news_scalping_lab.utils import canonical_json, sha256_text

PHASE7_TRANSPORT_HMAC_KEY_ENV = "NSLAB_PHASE7_TRANSPORT_HMAC_KEY"
PHASE7_TRANSPORT_ATTESTATION_VERSION = "nslab.phase7_transport_attestation.v1"
PHASE7_TRANSPORT_MINIMUM_KEY_BYTES = 32


def build_phase7_transport_attestation(
    *,
    run_id: str,
    trade_date: str,
    cutoff_at: str,
    embedded_artifacts: dict[str, Any],
    key_value: str | None = None,
) -> dict[str, str]:
    key = _phase7_transport_key(key_value)
    commitment = _phase7_transport_commitment(
        run_id=run_id,
        trade_date=trade_date,
        cutoff_at=cutoff_at,
        embedded_artifacts=embedded_artifacts,
    )
    commitment_json = canonical_json(commitment)
    return {
        "schema_version": PHASE7_TRANSPORT_ATTESTATION_VERSION,
        "algorithm": "HMAC-SHA256",
        "key_id": sha256_text(key.hex())[:16],
        "commitment_sha256": sha256_text(commitment_json),
        "signature": hmac.new(
            key,
            commitment_json.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest(),
    }


def verify_phase7_transport_attestation(
    attestation: Any,
    *,
    run_id: str,
    trade_date: str,
    cutoff_at: str,
    embedded_artifacts: dict[str, Any],
    key_value: str | None = None,
) -> bool:
    if not isinstance(attestation, dict):
        return False
    try:
        expected = build_phase7_transport_attestation(
            run_id=run_id,
            trade_date=trade_date,
            cutoff_at=cutoff_at,
            embedded_artifacts=embedded_artifacts,
            key_value=key_value,
        )
    except ValueError:
        return False
    return all(
        isinstance(attestation.get(field), str)
        and hmac.compare_digest(str(attestation[field]), expected[field])
        for field in (
            "schema_version",
            "algorithm",
            "key_id",
            "commitment_sha256",
            "signature",
        )
    )


def _phase7_transport_commitment(
    *,
    run_id: str,
    trade_date: str,
    cutoff_at: str,
    embedded_artifacts: dict[str, Any],
) -> dict[str, Any]:
    if not run_id.strip() or not trade_date.strip() or not cutoff_at.strip():
        raise ValueError("Phase 7 transport identity is incomplete")
    if not embedded_artifacts:
        raise ValueError("Phase 7 transport artifact closure is empty")
    return {
        "schema_version": "nslab.phase7_transport_commitment.v1",
        "run_id": run_id,
        "trade_date": trade_date,
        "cutoff_at": cutoff_at,
        "embedded_artifacts": embedded_artifacts,
    }


def _phase7_transport_key(key_value: str | None) -> bytes:
    value = (
        key_value
        if key_value is not None
        else os.environ.get(PHASE7_TRANSPORT_HMAC_KEY_ENV, "")
    )
    key = value.encode("utf-8")
    if len(key) < PHASE7_TRANSPORT_MINIMUM_KEY_BYTES:
        raise ValueError(
            f"{PHASE7_TRANSPORT_HMAC_KEY_ENV} must contain at least "
            f"{PHASE7_TRANSPORT_MINIMUM_KEY_BYTES} UTF-8 bytes"
        )
    return key
