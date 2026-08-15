# Production activation runbook

This runbook preserves the Phase 0-9 contracts. Preparation is not activation:
the local bootstrap does not import the production corpus, run the historical
shadow gate, publish a signed pointer, or switch the active release.

## 1. Deployment checkout preparation

Run these commands on the deployment PC from the repository root. The supported
official login command for the installed CLI is `codex login`; OAuth credential
files and tokens must never be read, copied, logged, or placed in `.env`.

```powershell
codex login
python -m news_scalping_lab.cli auth codex-status
python -m news_scalping_lab.cli production bootstrap-local `
  --evidence-policy csv-memory-only-strict `
  --llm-provider codex-oauth `
  --embedding-provider auto `
  --stock-web-path <STOCK_WEB_PATH>
python -m news_scalping_lab.cli production prepare-local `
  --stock-web-path <STOCK_WEB_PATH>
python -m news_scalping_lab.cli doctor --production-preflight
```

Expected policy identity:

```text
evidence_policy = csv-memory-only-strict
web_provider = disabled
web_required = false
embedding_fallback_policy = fail-closed
```

`<STOCK_WEB_PATH>` must contain both a valid price atlas and the validated
`atlas/research_daily` manifest, schema, calendar, access manifests, and snapshot
tree.

The bootstrap preserves existing non-empty HMAC keys unless
`--rotate-secrets` is explicit. It reports key fingerprints only. If a cloud
task cannot persist the deployment checkout, rerun the command on the actual
deployment PC and do not claim the local `.env` is prepared.

### Local embedding snapshot verification

The pinned SentenceTransformer download is selective. It includes the native
`model.safetensors`, tokenizer/configuration, `1_Pooling/config.json`, modules,
and model metadata required by the loader. It excludes `pytorch_model.bin` when
the safetensors weight is present, plus `tf_model.h5`, `onnx/**`, and
`openvino/**`. The exact selected set is content-addressed in
`memory/embedding_model_manifest.json` as sorted `(relative_path, size_bytes,
sha256)` entries.

`production prepare-local`, release finalization, deep doctor, and explicit
embedding audits perform deep verification by hashing every selected file. Daily
runtime performs fast verification: manifest and identity checks, existence and
size checks for every selected file, and SHA-256 checks for the critical weight,
config, modules, and tokenizer files. A process-local bounded cache reuses the
verified loaded model only while its model/revision/device/manifest/stat identity
is unchanged. Missing or changed files, non-finite smoke vectors, or a dimension
mismatch raise a production embedding failure; there is no deterministic or
BM25-only fallback.

To repeat the real download/load check in a newly created cache that is removed
after the run:

```powershell
$env:HF_HUB_DISABLE_PROGRESS_BARS = "1"
python tools/smoke_local_embedding.py
```

The latest measured result is recorded in
`diagnostics/local_embedding_clean_cache_smoke.json`; a fixture-only test is not
reported as a real model smoke.

## 2. Inventory and isolated import

Use the canonical repaired-corpus source or pass its exact manifest. First build
and inspect an inventory without mutating the live project, then seal it and
execute the isolated import.

```powershell
python -m news_scalping_lab.cli production build-inventory
python -m news_scalping_lab.cli production inspect-inventory --inventory <INVENTORY_JSON>
python -m news_scalping_lab.cli production seal-inventory --inventory <INVENTORY_JSON>
python -m news_scalping_lab.cli production stage-import --inventory <SEALED_INVENTORY_JSON>
python -m news_scalping_lab.cli production stage-import --inventory <SEALED_INVENTORY_JSON> --execute
python -m news_scalping_lab.cli production inspect-import --receipt <IMPORT_RECEIPT_JSON>
```

The command without `--execute` is the zero-write preflight. Do not continue if
the staged count, full-envelope root, import-loss checks, or batch inspection fail.

## 3. Production brain and semantic indexes

Run these commands against the isolated staged project. Catalog output is never
a production brain, and deterministic embeddings are never production indexes.

```powershell
python -m news_scalping_lab.cli brain rebuild --mode llm-full
python -m news_scalping_lab.cli brain audit --deep
python -m news_scalping_lab.cli memory rebuild-index --production
python -m news_scalping_lab.cli memory inspect-index
python -m news_scalping_lab.cli warehouse rebuild
python -m news_scalping_lab.cli warehouse verify
python -m news_scalping_lab.cli doctor --production --strict
```

The brain manifest must record `codex-oauth`, a successful OAuth health check,
at least one live agent call, and the exact embedding model/revision/artifact
identity. An embedding exception or identity drift must emit only a failure
receipt and no normal prediction, daily memory context, or final synthesis.

## 4. Phase 8 A-F shadow gate

Seal the historical dataset and split with the existing shadow commands, then
evaluate the canonical sealed dataset:

```powershell
python -m news_scalping_lab.cli memory seal-shadow-dataset <DATASET_JSON>
python -m news_scalping_lab.cli memory seal-shadow-split <SPLIT_JSON>
python -m news_scalping_lab.cli memory evaluate-shadow <SEALED_DATASET_JSON>
python -m news_scalping_lab.cli memory shadow-readiness
```

All A-F arms must use `csv-memory-only-strict` with zero BLIND web calls and zero
external web evidence. The required paired historical gate remains 40 days; a
smoke fixture or incomplete dataset is not a production pass.

## 5. Finalize and activate

Only after the import, llm-full brain, production memory index, stock-web checks,
and Phase 8 gate are all ready:

```powershell
python -m news_scalping_lab.cli production finalize-release `
  --receipt <IMPORT_RECEIPT_JSON> `
  --shadow-evaluation <SHADOW_EVALUATION_MANIFEST_JSON>
python -m news_scalping_lab.cli production inspect-release --manifest <RELEASE_MANIFEST_JSON>
python -m news_scalping_lab.cli production activate --manifest <RELEASE_MANIFEST_JSON>
python -m news_scalping_lab.cli production inspect-current --deep
python -m news_scalping_lab.cli production readiness
```

Activation is successful only when the authenticated current pointer resolves to
the inspected immutable release. To roll back, select an already validated prior
release manifest:

```powershell
python -m news_scalping_lab.cli production rollback --release-id <PRIOR_RELEASE_ID>
python -m news_scalping_lab.cli production inspect-current --deep
```

## 6. Daily operation

BLIND analysis must omit `--web-search`:

```powershell
python -m news_scalping_lab.cli analyze `
  --news <NEWS_CSV> `
  --trade-date YYYY-MM-DD `
  --cutoff YYYY-MM-DDT08:59:59+09:00 `
  --mode exhaustive
python -m news_scalping_lab.cli evaluate --trade-date YYYY-MM-DD
```

Optional web research is post-close only and requires
`POSTCLOSE_WEB_AUDIT_OPTIONAL`; its output is an isolated provenance artifact:

```powershell
python -m news_scalping_lab.cli audit postclose-web `
  --trade-date YYYY-MM-DD `
  --query <AUDIT_QUERY>
```
