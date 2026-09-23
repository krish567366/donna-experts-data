# Government bulk objects

This directory is the local object store for verified government PDF downloads.
PDF binaries are intentionally ignored by Git until Git LFS or external object
storage is configured. The versioned manifests under `METADATA/batches_bulk/`
retain canonical URLs, provenance, access status, byte counts, and SHA-256 hashes.

Run from the repository root:

```powershell
python negotiations/scripts/harvest_government_bulk.py --discover-frus --download --limit 2500
```

The command is resumable. It preserves prior successes, retries transient failures
up to the configured limit, writes via atomic temporary files, verifies `%PDF-`
and `%%EOF`, and deduplicates exports by SHA-256. It does not attempt to bypass
authentication, robots controls, rate limiting, or blocked endpoints.
