# Psychology corpus

This is a reproducible, license-aware acquisition system for a 10,000-document psychology corpus.

## Legal scope

Only copies carrying an allow-listed redistributable license are eligible for acquisition. A work being free to read is not treated as permission to redistribute. The pipeline never bypasses authentication or paywalls. Records without adequate rights remain metadata-only.

## Run

```powershell
python acquire.py discover --target 10000 --email you@example.com
python acquire.py download --limit 10000
python acquire.py export
python acquire.py verify
```

Set `OPENALEX_API_KEY` for a sustained 10,000-file OpenAlex content download. Discovery can run without it. The downloader is checkpointed and safe to resume.

## Parallel, shard-safe downloads

`tools/sharded_download.py` partitions records by a stable hash of `SOURCE_ID`.
Run one process per shard with the same shard count and a different zero-based index:

```powershell
0..7 | ForEach-Object -Parallel {
  python tools/sharded_download.py --shard-count 8 --shard-index $_ --workers 4 --rate 2
} -ThrottleLimit 8
```

Each process touches only its disjoint corpus paths and writes an atomic result CSV to
`logs/shards/`; it never rewrites `psychology_sources.csv`. `--rate` is the aggregate
request rate for one process, so eight processes at `--rate 2` can issue 16 requests
per second. Retries honor `Retry-After`, downloads and sidecars use atomic renames,
and credentials are removed from provenance URLs. Use `--limit 10` for a smoke test.
Do not run the same shard index twice against the same corpus directory.

## Layout

- `config/constructs.csv`: acquisition taxonomy and search streams
- `psychology_sources.csv`: source manifest
- `psychology_sources.parquet`: generated when `pyarrow` is installed
- `corpus/<SOURCE_ID>/original.pdf`: acquired original (local/object-store material; ignored by Git)
- `corpus/<SOURCE_ID>/metadata.json`: normalized source metadata
- `corpus/<SOURCE_ID>/provenance.json`: retrieval and hash record
- `logs/failures.csv`: retryable and terminal failures

PDFs must be placed in durable object storage or a Git-LFS service with adequate quota. Do not force-add them to normal Git.
