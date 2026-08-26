from __future__ import annotations

import pytest

import news_scalping_lab.brain.category_index as category_index
from news_scalping_lab.brain.category_index import (
    _claim_inclusion_proofs,
    verify_category_claim_inclusion_proof,
)
from news_scalping_lab.utils import sha256_text


def _ledger_rows(count: int) -> list[dict[str, str]]:
    return [
        {
            "claim_id": f"CLAIM-{index:04d}",
            "claim_sha256": sha256_text(f"payload-{index}"),
        }
        for index in reversed(range(count))
    ]


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5, 8, 17])
def test_claim_inclusion_proofs_verify_for_balanced_and_odd_trees(count: int) -> None:
    rows = _ledger_rows(count)

    proofs, merkle_root = _claim_inclusion_proofs(rows)

    assert set(proofs) == {str(row["claim_id"]) for row in rows}
    assert all(
        verify_category_claim_inclusion_proof(proof, merkle_root)
        for proof in proofs.values()
    )


def test_claim_inclusion_proofs_hash_each_merkle_level_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = _ledger_rows(257)
    hash_call_count = 0
    original_sha256_text = category_index.sha256_text

    def counted_sha256_text(value: str) -> str:
        nonlocal hash_call_count
        hash_call_count += 1
        return original_sha256_text(value)

    monkeypatch.setattr(category_index, "sha256_text", counted_sha256_text)

    proofs, _merkle_root = _claim_inclusion_proofs(rows)

    assert len(proofs) == len(rows)
    assert hash_call_count < len(rows) * 3
