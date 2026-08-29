# QUALITY_FULL Representative Distribution Fix

## Decision

The representative-selection tolerance bypass is removed.

`QUALITY_FULL` keeps the distribution share-error tolerance fixed at `0.25`. It may use a larger physical representative JSONL envelope (`48,000` bytes instead of `24,000`), but it cannot raise the tolerance to match an observed error.

This is mechanically verified diagnostic evidence. It is not a predictive-quality result and cannot support model selection, production activation, or a V0/V1 winner claim.

## Actual Regression Population

The fix was replayed against the population that stopped the invalidated V1 run:

```text
population       POP-EFE27D6D871A60A72FF6
cluster          EVCL-88fec7617a87d4a9
memory snapshot  MEMIDX-4409624afdffd1d01018
records / units  140 / 138
candidate pool   138
adaptive target  11
```

| Field | Old artifact | New artifact |
|---|---:|---:|
| Representative set | `REP-4194F6A4300CA656A62D` | `REP-E8BA22597AEB25B4586F` |
| Selected records | 12 | 14 |
| Serialized JSONL bytes | 23,113 / 24,000 | 33,944 / 48,000 |
| Distribution share error | 0.2971014493 | 0.2256728778 |
| Recorded tolerance | 0.2971014493 | 0.25 |
| Contract result | Invalid tolerance inflation | PASS |

The old implementation recorded `max(0.25, observed_error)` as its tolerance. The new implementation selects two additional independent units and reaches the fixed contract without changing the tolerance.

## Immutable Anchors

```text
old manifest SHA-256
283f25765f5b08206bdf5c48d3921f79f87442a33223c5a24bb66ce1b6d0210a

new manifest SHA-256
cd57b56ef625a79105e935367bf2ca16aa6498bf35ee134ea21dabb2b7dacd96

new representative records SHA-256
d588c189d9bae1bb32b46d5f66398f18d978a41f823973c1a6976744e3b76db4
```

The new manifest passed a complete `RepresentativeSelector.inspect` recomputation with no errors using the pinned production MiniLM embedding identity. The replay made zero live LLM calls and did not mutate production.

## Cache Boundary

The new selection version is:

```text
stratified_mmr_facility.v3.quality_full_extended_pack
```

The selection version and `48,000`-byte envelope are part of the representative-set identity. Artifacts created by the old `quality_full_distribution_observed` bypass cannot be resumed as current `QUALITY_FULL` representative packs.
