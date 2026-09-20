# Negotiation corpus

This directory contains legally acquired, provenance-tracked source material for a specialist negotiation corpus. Originals are preserved unchanged; derived text, OCR, translations, and annotations belong beside—but never overwrite—the original.

## Layout

- `PAPERS`, `UNIVERSITY`: negotiation science and teaching material
- `GOVERNMENT`: government and declassified records
- `INTERNATIONAL`: international organizations and treaty records
- `TRANSCRIPTS`: dialogue, memcons, oral histories, and hearing transcripts
- `BUSINESS`, `MA`, `VC`, `PHARMA`, `PROCUREMENT`, `LABOR`, `LEGAL`: commercial and legal material
- `CRISIS`, `PEACE`, `FAILED`: operational and historical cases
- `BOOK_METADATA`, `EXERCISES`, `AUDIO_VIDEO`: discovery metadata and legally reusable material
- `METADATA`: schemas, batch manifests, queues, reports, and indexes
- `scripts`: deterministic merge and validation utilities

Each source directory should contain the untouched acquired object (`original.pdf`, or its native extension), `metadata.json`, and optionally derived files. The master CSV is generated from batch manifests; it is not hand-edited.

## Rebuild and validate

```powershell
python negotiations/scripts/merge_manifests.py
python negotiations/scripts/validate_corpus.py
```

The validator checks file existence, signatures, SHA-256 hashes, duplicate hashes, and required provenance fields. Pointer-only records are allowed only when legal access is restricted or a legitimate download failed, and must carry the corresponding access status.
