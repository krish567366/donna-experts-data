# International bulk corpus

This directory is populated by `scripts/harvest_international_bulk.py`. Native
PDF binaries are intentionally excluded from Git until the repository adopts
Git LFS or external object storage. The tracked acquisition manifest records
official source URLs, provenance, SHA-256 and validation status.

Run a discovery-only pass:

```sh
python negotiations/scripts/harvest_international_bulk.py --target 2500 --discover-only
```

Acquire and verify 2,500 unique PDFs (resumable):

```sh
python negotiations/scripts/harvest_international_bulk.py --target 2500 --workers 12
```

Additional official-repository CSV or JSONL exports can be passed repeatedly
with `--url-list`. Each row must contain `url`; provider metadata is strongly
recommended. Failed, oversized, non-PDF and duplicate responses are logged.
