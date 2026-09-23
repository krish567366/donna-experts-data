# Acquisition report — 10,000-PDF milestone

Completed: 2026-09-23

- 10,172 locally retained PDF files
- 10,152 globally unique PDFs by SHA-256
- 20 duplicate file copies identified without deleting source variants
- 0 invalid PDF signatures
- approximately 17.23 GB of source PDFs
- 6,430 international/institutional PDFs
- 3,038 open scholarly PDFs
- 643 U.S. government/archive PDFs
- 41 curated seed PDFs
- 11 GitHub Release ZIP shards, each below the 2 GiB asset limit

The canonical machine-readable index is `negotiation_bulk_sources.csv` with a
Parquet equivalent. `METADATA/release_shard_index.csv` maps every unique PDF to
its content-addressed member inside a release shard. `SHA256SUMS.txt` verifies
the release assets themselves.

All retained objects passed `%PDF-` signature validation and full SHA-256
deduplication. Failed downloads, HTML wrappers, blocked endpoints, and sources
without retrievable PDF bytes are excluded from the acquired corpus.
