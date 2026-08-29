# QUALITY_FULL Invalidated Run Report

## Decision

`QPRED-4ecc6155c077cb5b092c` is `INVALIDATED_DIAGNOSTIC_ONLY`.

It is not eligible for scoring, model comparison, promotion, or production activation. No final score exists. The invalidation did not modify or delete the original artifacts, and production remains `NOT_PRODUCTION_ACTIVATED`.

Reason code:

```text
UNSAFE_PREDICTION_BOUNDARY_READ_NORMALIZED_INDEX_WITH_OUTCOME_METADATA
```

## Boundary Finding

The prediction-input preparation boundary deserialized `normalized_episode_index.json`. That file was not a blind-only input: it contained `available_from=2026-01-05T00:00:00+09:00`, training-eligible counts, outcome-derived record-type counts, and a winner-census field, while the prediction cutoff was `2026-01-02T08:59:59+09:00`.

This proves an unsafe prediction boundary. It does not prove that an outcome-ledger payload was shown to the LLM. A separate scan of 5,687 prediction-related files and 1,950,764,054 bytes found zero matches for the selected outcome hashes or paths. The run is invalid because the boundary itself was unsafe, not because direct truth-payload exposure was demonstrated.

## Frozen References

| Artifact | SHA-256 |
|---|---|
| Blind selection | `4f30c5f14283304e6b46c836437c67f9081543550193160d974f4dde932ebfab` |
| Paired prediction manifest | `ff49636ac2204a869c6ef045815d0f4858ec487b40b9ea0fb655f7a7b73b5155` |
| V0 prediction seal | `c6896fd4cc83300013113d4ed0e2b55521117085e44486967a1d7ae95e55840a` |

The full invalidation manifest is outside the repository at:

```text
C:/Users/eorb9/projects/nslab_semantic_upgrade_v7_eval_v2/project/runs/semantic_brain_upgrade/quality_full/invalidated/QPRED-4ecc6155c077cb5b092c/invalidation_manifest.json
SHA-256 648cb5aec7fa68bba175f878f8fdc4007bb87644c70287e45acb33f32b3b7ef9
```

## Frozen Progress

- Expected cases: 3
- Expected V0/V1 seals: 6
- Completed seals: 1 (`16.6667%`)
- Completed paired cases: 0
- V0: `RUN-353e3f1c7604`, sealed but invalid for scoring through run ancestry
- V1: `RUN-1230ac0bdd29`, 99 of 478 cluster checkpoints (`20.7113%`)
- Next partial V1 cluster: ordinal 100, `EVCL-0e602855636efe4e`
- V1 prediction, context manifest, evidence memos, and seal: absent
- Cases 2 and 3: not started

The 99 completed V1 checkpoints closed over 2,122 JSON documents and 6,663 SHA-256 references with zero integrity errors. The incomplete state is preserved for diagnosis; it is not a valid evaluation result.

## Calls And Time

| Scope | Calls |
|---|---|
| Shared pre-retrieval | 44 logical, 26 live OAuth, 18 checkpoint hits |
| Final sealed V0 replay | 5 logical, 5 checkpoint hits, 0 new OAuth |
| Earlier V0 pre-seal attempts | 11 non-hit traces: 10 success, 1 error, 1 recorded retry |
| V1 partial | 0 live `gpt-5.6-sol` calls, 0 V1 LLM traces |

The old V1 partial began its setup artifact at `2026-08-29T07:56:19.473533+09:00`, wrote its first retrieval artifact at `07:57:52.768706`, and reached the initial partial-stop artifact at `14:45:54.440225`. That is 24,574.966692 seconds from setup and 24,481.671519 seconds across the retrieval-write interval. V1 work in this interval was local MiniLM, DuckDB/FTS, and deterministic representative selection.

The apparent query-text mojibake was a PowerShell rendering issue. Source and derived artifacts were valid UTF-8, contained no replacement characters, and retained matching query hashes.

## Reuse Boundary

Allowed only:

- forensic review of the immutable invalidated artifacts;
- engineering and latency diagnostics explicitly labeled invalid;
- independently pre-existing evaluation brain, memory, and corpus artifacts.

Prohibited:

- scoring this paired prediction manifest;
- claiming predictive quality or retrieval value from this run;
- automatically reusing its downstream caches in a formal rerun;
- model selection, promotion, final brain selection, or production activation.

A formal rerun requires a new blind-only input boundary, a new run identity, and prediction code that cannot resolve, hash, stat, or deserialize normalized or outcome artifacts.
