# QUALITY_FULL Sealed D-1 Preparation

## Decision

The three-case `QUALITY_FULL` selection was regenerated with one immutable, cutoff-safe D-1 market artifact inside each `QINPUT` package.

```text
selection  QSEL-19b3c80ba392db8564c9
SHA-256   bfcc21e433ab36cf457254f7587f7fb71d2a12e4a332a12c0fead0b0554d3f9c
elapsed   140.471 seconds
LLM/OAuth calls  0 / 0
```

Strict selection reload and strict `SharedDMinusOneContext` parsing passed. The outcome selection remains physically separate and reports `available_to_prediction_process=false`.

## Sealed Cases

| Trade date | Latest sealed session | Snapshots | Payload bytes | D-day/future | Duplicate ticker |
|---|---|---:|---:|---:|---:|
| 2026-01-02 | 2025-12-30 | 2,759 | 567,093 | 0 | 0 |
| 2026-03-24 | 2026-03-23 | 2,758 | 568,539 | 0 | 0 |
| 2026-03-16 | 2026-03-13 | 2,746 | 565,978 | 0 | 0 |

Every case also has zero skipped ticker identities, zero prediction-time price repository accesses, and zero outcome accesses. The shared StockWeb source revision is:

```text
7cb4dbd1cdaaec846315be849716df458e6f2ff063322f07c7fef7f2d3b6a472
```

## Artifact Roots

```text
2026-01-02
QINPUT manifest  b35050566f29159b4c40bb2cc95c596ba31ae26c2216e8a711ced0f9ddbfd824
D-1 file         a91cb1f7d0ddf207594182ddf30cdb9f45528a58978cc88087e9e80f4cbafa62
universe root    f641fd9604753a4453827013f5924d68ccabca50659aed8fa0e33eeb8c7fcd85
snapshot root    e366537d730bb523a15668e60a8c38d0b84fb0d44e817cc3c81d2544b767710a

2026-03-24
QINPUT manifest  381d92d79b62fcc806517be7b1f08ec38b480ec3e292787762c5b4b0d7b00c9a
D-1 file         61427fd39d248738d9afb1afc208330ff2390c7b4af15b0a0dfd0a2b7ae8a64d
universe root    a2dfc639a250c652b056a9bf31ada3692036c0342dcbe2e1492f970561db0767
snapshot root    f4fd77124a22ab2a35883052f4c4fe28f884352153a4c50daf02ed89702bc4d1

2026-03-16
QINPUT manifest  a5e43d9a4e77233f355ee71c01ade69f2cabcb848933dd7f35148046619086e9
D-1 file         a35a5572a69e605f70a15820934aa2c1f632d09fa61b3ded978761590ce07be6
universe root    25826ce21fd3a2bf7de24785e8c7439e76e4fbf49777bdcb3716a8d07db1c390
snapshot root    f3c58b05a10c40c9b127a374b97f9b0d8f0a104465f7a7f83f2144e58302e890
```

## Prompt Boundary

The full sealed artifacts total 1,701,610 payload bytes and remain the immutable universe commitment. They are not copied wholesale into final-synthesis prompts. The prompt receives a strict `DMinusOnePromptProjection` containing every preliminary candidate ticker disposition, its exact sealed row when present, missing ticker IDs, and the full artifact roots.

A 2,800-snapshot regression confirms that unrequested rows do not enter the prompt projection and that the bounded projection stays below the configured 80,000-token conservative byte limit.

## Verification Scope

The D-1 preparation/loader regressions, the quality runtime unit suite, Ruff, Mypy over 135 source files, and schema parity passed. The full synthetic QPRED-to-score integration regression also passed while spies prohibited construction or reopening of the privileged price source. This verifies the boundary mechanics; it did not perform OAuth inference or establish predictive quality.

The actual three-case `QSEL-19b3c80ba392db8564c9` QPRED-to-score run is `NOT_RUN`. No paired prediction manifest or score report exists for this selection.

This report is preparation evidence only. It makes no predictive-quality, model-selection, or production-activation claim.
