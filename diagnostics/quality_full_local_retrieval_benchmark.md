# QUALITY_FULL Local Retrieval Benchmark

This report records bounded LOCAL-only observations from the current V1
retrieval path against the evaluation project brain and memory. It does not
contain a predictive runtime conclusion or a production activation conclusion.

## One Material Cluster

- Run: `V1LOCAL-20260829T195400-5ac7b1fe`
- Wall: `33.3282115 s`
- Process CPU: `110.421875 s`
- Peak RSS: `7,034,241,024 bytes` (`6.550 GiB`, approximately `7.034 GB` decimal)
- Selected records: `96`
- Locally prepared prompt batches: `6`
- LLM/OAuth calls: `0`
- LLM-exposed/final-cited records: `0/0`

The run stopped before any provider call. It verifies local selection, exact
record loading, and prompt preparation only.

## Two Material Clusters

| Setting | Wall | Process CPU | Peak RSS |
| --- | ---: | ---: | ---: |
| `max_cluster_workers=1` | `61.970729 s` | `202.625 s` | `7,254,351,872 bytes` |
| `max_cluster_workers=2` | `49.702268 s` | `210.875 s` | `9,195,286,528 bytes` |

Observed worker-2/worker-1 comparison:

- Wall speedup: `1.247x` (`19.8%` less wall time)
- Peak RSS increase: `1,940,934,656 bytes` (`1.808 GiB`)
- Canonical selected record/lane/stage semantic parity: `true`
- Combined semantic root: `dbd3419eba5c9eb638b9b45dbafe993f26a70f5c60a70e199e6555bcb4eedd1a`
- Artifact byte-size multiset parity: `true`
- Raw artifact hash parity: `false`

Raw hashes are expected to differ because fresh run IDs and artifact paths are
committed into provenance-bearing outputs. The semantic comparison removes that
run-specific identity surface; it does not claim byte-identical artifacts.

## Measurement Limits

- Every measured arm used a fresh process and fresh run identity.
- Invalid preliminary runs and `RUN-1230` caches were not reused.
- The OS file cache was not flushed.
- Worker 1 ran before worker 2, so order and warm-cache bias are possible.
- Fresh clustering and all LLM/OAuth time are excluded.
- LLM/OAuth call count was `0` throughout.
- Production artifacts and production activation state were unchanged.
- These one- and two-cluster observations do not support a predictive runtime,
  throughput guarantee, or production conclusion.

## External Anchors

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\runs\diagnostics\V1LOCAL-20260829T195400-5ac7b1fe\benchmark.json` | `10,479` | `6bc996490e390b48dacef445ffa337d9867bde40572491bc6c2d426d08e53afb` |
| `C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\runs\diagnostics\V1LOCAL-20260829T195400-5ac7b1fe\benchmark.md` | `1,603` | `a005d3688a32bc4145812288ce5ff71779c0745a0b7d359a788137403600bf1d` |
| `C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\runs\diagnostics\V1W1-20260829T195627-a2167d4d\worker_benchmark.json` | `4,930` | `3e10ead24bb8865995e863a3afa13be0621d75f1174c0b8ae94f4a3740933070` |
| `C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\runs\diagnostics\V1W2-20260829T195805-3f8219dc\worker_benchmark.json` | `4,929` | `291761dbaac7fc40aeef4f1afd2f261431f61cc5911a62ad1cd27249773a18e4` |
| `C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\runs\diagnostics\worker_compare_harness\comparison.json` | `2,678` | `a628d3e39847152cccab1f064b1c0e97acf27aa2717a9742c8800e10755bc36f` |
| `C:\Users\eorb9\projects\nslab_semantic_upgrade_v7_eval_v2\project\runs\diagnostics\worker_compare_harness\comparison.md` | `633` | `677dd0f9d596b7eebc4662ae814506d94748eab71a324e93d52cfbe2c6d7daa2` |
