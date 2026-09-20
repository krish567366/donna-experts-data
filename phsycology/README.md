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

## Layout

- `config/constructs.csv`: acquisition taxonomy and search streams
- `psychology_sources.csv`: source manifest
- `psychology_sources.parquet`: generated when `pyarrow` is installed
- `corpus/<SOURCE_ID>/original.pdf`: acquired original (local/object-store material; ignored by Git)
- `corpus/<SOURCE_ID>/metadata.json`: normalized source metadata
- `corpus/<SOURCE_ID>/provenance.json`: retrieval and hash record
- `logs/failures.csv`: retryable and terminal failures

PDFs must be placed in durable object storage or a Git-LFS service with adequate quota. Do not force-add them to normal Git.

