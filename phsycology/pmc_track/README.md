# PMC Open Access acquisition track

This isolated track discovers negotiation-relevant psychology articles with the
official NCBI E-utilities API, confirms inclusion through NLM's official public
PMC AWS dataset, reads the per-version license metadata, downloads available
PDFs, validates their signatures, and records SHA-256 checksums.

Run: `python acquire_pmc.py --candidates 100 --download 20`

`candidates.csv` retains provenance, PMC-supplied license codes, download
status, and failures. Redistribution rights vary by article; preserve each
article's license notice and consult the PMC OA terms before reuse.
