# Local embedding clean-cache smoke

- Measured at: `2026-08-15T16:54:00+09:00`
- Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`
- Revision: `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`
- Result: `PASS`
- Selected snapshot: 12 files, 499,561,295 bytes (476.419 MiB)
- Full repository size reported by Hugging Face: 4,620,943,233 bytes (4.304 GiB)
- Download reduction: 4,121,381,938 bytes, 89.189%
- Excluded repository files: 16
- Embeddings: 2 vectors, 384 dimensions, all finite
- Clean download plus load: 38.938047 seconds
- SentenceTransformer load: 2.025815 seconds
- Deep verification: 0.344066 seconds, 12 files hashed
- First fast verification: 0.335193 seconds
- Same-process cached fast verification: 0.000310 seconds, 0 files rehashed
- Peak memory: not measured (`null`)
- Manifest path: `memory/embedding_model_manifest.json`
- Artifact root: `e0da458bb4f008d3c9fbf6dbff0fe0a482c025dfd7b759ef0294fb978d9eeca0`

The smoke used a newly created temporary project and Hugging Face cache. The
temporary model bytes were removed after verification; no existing repository
cache was reused.
